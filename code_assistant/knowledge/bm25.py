"""BM25 child retrieval adapted from Agent_MCP_RAG; consistent identifier tokenization."""
import re
from rank_bm25 import BM25Okapi


def tokenize(text):
    parts = re.findall(r"[A-Za-z_][A-Za-z_0-9.]*|[\u4e00-\u9fff]", text)
    tokens = []
    for word in parts:
        tokens.append(word.lower())
        if len(word) > 1:
            tokens.extend(x.lower() for x in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)", word)
                          if x.lower() != word.lower())
    return tokens


def search(rows, query, limit=20):
    if not rows:
        return []
    corpus = [tokenize(r["heading"] + "\n" + r["text"]) or ["__empty__"] for r in rows]
    model = BM25Okapi(corpus)
    terms = tokenize(query)
    scores = model.get_scores(terms)
    # Negative/zero BM25 is valid for common terms in small corpora. Filter by lexical
    # overlap, not score > 0 (the source project discarded these valid matches).
    matches = [i for i, words in enumerate(corpus) if set(words).intersection(terms)]
    ranked = sorted(matches, key=lambda i: (-float(scores[i]), rows[i]["id"]))
    return [(rows[i]["id"], float(scores[i])) for i in ranked[:limit]]
