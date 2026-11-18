"""Explicit network adapters with bounded requests and observable unknown usage."""
import math
import os
import time
import httpx
from code_assistant.knowledge.repository import digest


def request_json(url, key, body, ledger, role, allowed):
    if not allowed:
        raise ValueError("external_retrieval_disabled")
    if len(ledger) >= 200:
        raise ValueError("external_call_budget")
    event = {"role": role, "model": body["model"], "usage": None, "status": "started",
             "input_chars": len(str(body)), "duration_ms": 0}
    ledger.append(event)
    started = time.monotonic()
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            with client.stream("POST", url, headers={"Authorization": "Bearer " + key}, json=body) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 2 * 1024 * 1024:
                        raise ValueError("response_too_large")
                import json
                result = json.loads(content)
        event["usage"] = result.get("usage") if isinstance(result.get("usage"), dict) else None
        event["status"] = "ok"
        return result
    except Exception as exc:
        event.update(status="error", error_class=type(exc).__name__)
        raise
    finally:
        event["duration_ms"] = int((time.monotonic() - started) * 1000)


class Embedder:
    def __init__(self):
        self.url = os.getenv("KNOWLEDGE_EMBEDDING_URL", "")
        self.key = os.getenv("KNOWLEDGE_EMBEDDING_KEY", "")
        self.model = os.getenv("KNOWLEDGE_EMBEDDING_MODEL", "")
        self.dimensions = int(os.getenv("KNOWLEDGE_EMBEDDING_DIMENSIONS", "0"))
        self.profile = digest([self.url, self.model, self.dimensions])

    @property
    def configured(self):
        return bool(self.url and self.model and 1 <= self.dimensions <= 4096)

    def embed(self, text, ledger, allowed=False):
        if not self.configured:
            raise ValueError("embedding_not_configured")
        if len(text) > 12000:
            raise ValueError("embedding_input_limit")
        data = request_json(self.url, self.key, {"model": self.model, "input": text}, ledger, "embedding", allowed)
        vector = data["data"][0]["embedding"]
        if len(vector) != self.dimensions or not all(
                isinstance(x, (float, int)) and not isinstance(x, bool) and math.isfinite(x) for x in vector):
            raise ValueError("invalid_embedding")
        if not any(vector):
            raise ValueError("zero_embedding")
        return vector
