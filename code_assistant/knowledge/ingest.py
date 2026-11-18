"""Explicit local ingestion. Preview first; external embedding requires --allow-external."""
import argparse
import json
from pathlib import Path
from code_assistant.knowledge.contracts import DocumentInput
from code_assistant.knowledge.embeddings import Embedder
from code_assistant.knowledge.repository import KnowledgeRepository
from code_assistant.persistence.database import Repository
from backend.settings import ROOT, database_url


def load_manifest(path):
    path = Path(path).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    result = []
    for item in manifest["documents"]:
        source = (path.parent / item["file"]).resolve()
        if not source.is_relative_to(path.parent):
            raise ValueError("source_outside_manifest_directory")
        result.append(DocumentInput(**{k: v for k, v in item.items() if k != "file"},
                                    text=source.read_text(encoding="utf-8")))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "knowledge_sources/lcel/manifest.json")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-external", action="store_true")
    parser.add_argument("--max-calls", type=int, default=200)
    args = parser.parse_args()
    documents = load_manifest(args.manifest)
    from code_assistant.knowledge.chunking import split_parent_child
    count = sum(sum(len(p.children) for p in split_parent_child(d.text)) for d in documents)
    print(json.dumps({"documents": len(documents), "children": count,
        "embedding_call_ceiling": count if args.allow_external else 0, "apply": args.apply}, ensure_ascii=False))
    if args.apply:
        repo = Repository(database_url())
        try:
            result = KnowledgeRepository(repo.engine).ingest(documents,
                Embedder() if args.allow_external else None, args.max_calls, args.allow_external)
            print(json.dumps(result, ensure_ascii=False))
        finally:
            repo.engine.dispose()


if __name__ == "__main__":
    main()
