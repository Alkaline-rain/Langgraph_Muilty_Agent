from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class RetrievalArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    focus: str = Field(default="", max_length=1000)
    limit: int = Field(default=3, ge=1, le=5)


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["bm25", "dense", "hybrid", "rerank"] = "rerank"
    max_retrievals: int = Field(default=2, ge=0, le=5)
    context_chars: int = Field(default=6000, ge=500, le=12000)
    # Online network use is opt-in; embedding/rerank endpoints never share LLM credentials.
    allow_external: bool = False


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_uri: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=300)
    domain: Literal["lcel"] = "lcel"
    library: str = "langchain-core"
    supported_versions: list[str] = Field(min_length=1)
    source_revision: str
    license_note: str
    text: str = Field(min_length=1, max_length=500000)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    parent_id: str
    child_ids: list[str]
    version_id: str
    source_uri: str
    title: str
    heading: str
    start_line: int
    end_line: int
    content_hash: str
    text: str
    score: float
    score_type: str
    child_ranks: dict = Field(default_factory=dict)
