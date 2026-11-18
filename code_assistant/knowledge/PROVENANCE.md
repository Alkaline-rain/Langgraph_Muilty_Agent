# Algorithm provenance

Adapted concepts from the user-owned sibling Agent_MCP_RAG project. No runtime sibling imports, cached results, queue, MCP code, endpoint or credentials migrated.

- `Src/augmented/chunking.py`: SHA256 `65ba30a53d5c07e32c3f8517c1d754ac40dc09e7a783f1cbbf89c490b4dad6cf`
- `Src/augmented/bm25.py`: SHA256 `718ddffadbd5f39ba4fdd3d9bd0ff970b8225256969711a835fd7ce8f4f6ae7f`
- `Src/augmented/hybrid_retriever.py`: SHA256 `85acfa34b844fed0e0bb7ecabe176065a3a2a71d25754ce6c771518618e2b6e7`
- `Src/augmented/reranker.py`: SHA256 `37429ab66468cad4326a02a250490ee324e82b49447946db3075bb5de4feec95`

Changes: fence-aware exact spans; reject oversized atoms; identifier tokenization; lexical overlap handles non-positive BM25; max child RRF per parent; native vector storage; immutable snapshots; strict rerank validation and observable fallback.
