# rag.py -- Block 4: retrieval over the course's own policy documents, with a citation id for every chunk.
# Adapted from my Lab 12 tools.py (build_index, search_handbook). New here: staff-only documents are tagged
# with "<!-- audience: staff -->" and are NEVER indexed, so a student's search cannot retrieve them at all.
import json, os
import numpy as np
import config

STAFF_TAG = "<!-- audience: staff -->"


def chunk_docs(docs_dir=None):
    """Split every student-facing docs/*.md file at its '## ' headings. Chunk ids look like late_policy.md#2."""
    docs_dir = docs_dir or config.DOCS_DIR
    chunks, skipped = [], []
    for name in sorted(os.listdir(docs_dir)):
        if not name.endswith(".md"):
            continue
        text = open(os.path.join(docs_dir, name), encoding="utf-8").read()
        if text.lstrip().startswith(STAFF_TAG):
            skipped.append(name)                    # staff only: not searchable by students
            continue
        title = text.splitlines()[0].lstrip("# ").strip()
        for n, section in enumerate(text.split("\n## ")[1:], start=1):
            heading, _, body = section.partition("\n")
            chunks.append({"id": f"{name}#{n}", "source": name,
                           "text": f"{title} - {heading.strip()}: {' '.join(body.split())}"})
    return chunks, skipped


def embed(client, texts, prefix):
    """Unit-length vectors from nomic-embed-text, plus the embedding tokens Ollama reports."""
    resp = client.embeddings.create(model=config.EMBED_MODEL, input=[prefix + t for t in texts])
    v = np.array([d.embedding for d in resp.data])
    tokens = resp.usage.prompt_tokens if resp.usage else 0
    return v / np.linalg.norm(v, axis=1, keepdims=True), tokens


def build_index(client):
    chunks, skipped = chunk_docs()
    vectors, _ = embed(client, [c["text"] for c in chunks], config.DOC_PREFIX)
    for c, v in zip(chunks, vectors):
        c["embedding"] = v.tolist()
    os.makedirs(os.path.dirname(config.INDEX_PATH), exist_ok=True)
    with open(config.INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(chunks, f)
    return len(chunks), sorted({c["source"] for c in chunks}), skipped


_INDEX = None


def load_index():
    global _INDEX
    if _INDEX is None:
        if not os.path.exists(config.INDEX_PATH):
            raise FileNotFoundError("the policy index is missing: run `python main.py index` first")
        chunks = json.load(open(config.INDEX_PATH, encoding="utf-8"))
        _INDEX = (chunks, np.array([c["embedding"] for c in chunks]))
    return _INDEX


def search(client, query, top_k=None, min_sim=None):
    """Returns (text for the model, [chunk ids], embedding tokens). Only chunks above min_sim are returned."""
    chunks, matrix = load_index()
    q, tokens = embed(client, [query], config.QUERY_PREFIX)
    scores = matrix @ q[0]
    order = np.argsort(scores)[::-1][:top_k or config.RAG_TOP_K]
    hits = [chunks[i] for i in order if scores[i] >= (min_sim if min_sim is not None else config.RAG_MIN_SIM)]
    if not hits:
        return "No policy section matches this question.", [], tokens
    return "\n".join(f"[{c['id']}] {c['text']}" for c in hits), [c["id"] for c in hits], tokens
