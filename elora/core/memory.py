"""
Elora Long-Term Memory Engine.

Provides a persistent, semantic memory store backed by ChromaDB (HNSW graph index)
and sentence-transformers embeddings. Memories are organised into topic nodes and
retrieved via approximate nearest-neighbour search — typical query latency ~20 ms.

Design principles:
- Memory is NEVER auto-injected. Only retrieved on explicit trigger.
- Low-confidence matches (< threshold) are silently discarded to prevent hallucination.
- Each memory carries a timestamp so the LLM can reason about recency.
- Topic tagging groups memories into browsable category nodes.
"""

import logging
import os
import uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger("elora.memory")

# ── Persistence paths ──────────────────────────────────────────────────────────
MEMORY_DIR = os.path.expanduser("~/.config/elora/memory")
COLLECTION_NAME = "elora_memories"

# ── Retrieval tuning ───────────────────────────────────────────────────────────
# Only return results whose cosine similarity score exceeds this value.
# Range 0–1. 0.75 means "at least 75% semantically similar".
DEFAULT_SIMILARITY_THRESHOLD = 0.75
DEFAULT_TOP_K = 5

# ── Lazy singletons (loaded once, cached for the process lifetime) ─────────────
_chroma_client = None
_collection = None
_embed_model = None


# ── Internal helpers ───────────────────────────────────────────────────────────

def is_memory_available() -> tuple[bool, str]:
    """
    Checks if memory dependencies are installed on the system.
    Returns (True, "") or (False, "error message").
    """
    try:
        import chromadb
        import torch
        from sentence_transformers import SentenceTransformer
        return True, ""
    except ImportError as e:
        return False, f"Memory dependencies are missing: {e!s}. Please run `uv sync` when your internet connection is stronger."


def _get_chroma_collection():
    """
    Returns the ChromaDB collection, initialising the client and collection on
    first call.  Uses a local persistent directory so memories survive restarts.
    """
    global _chroma_client, _collection
    if _collection is not None:
        return _collection

    try:
        import chromadb
        from chromadb.config import Settings
    except ImportError:
        raise RuntimeError(
            "ChromaDB is not installed. Please run `uv sync` when you have a stable internet connection to install it."
        )

    os.makedirs(MEMORY_DIR, exist_ok=True)
    logger.info("Initialising ChromaDB at %s", MEMORY_DIR)

    _chroma_client = chromadb.PersistentClient(
        path=MEMORY_DIR,
        settings=Settings(anonymized_telemetry=False),
    )
    _collection = _chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        # Cosine distance → higher score = more similar
        metadata={"hnsw:space": "cosine"},
    )
    logger.info("ChromaDB collection '%s' ready (%d entries)", COLLECTION_NAME, _collection.count())
    return _collection


def _get_embed_model():
    """
    Lazy-loads the sentence-transformer embedding model (all-MiniLM-L6-v2).
    ~90 MB download on first use, then cached by HuggingFace locally.
    Inference: ~10 ms per sentence on CPU.
    """
    global _embed_model
    if _embed_model is not None:
        return _embed_model

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise RuntimeError(
            "SentenceTransformers is not installed. Please run `uv sync` when you have a stable internet connection to install it."
        )

    logger.info("Loading sentence-transformer embedding model (first use may download ~90 MB)...")
    _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
    logger.info("Embedding model loaded.")
    return _embed_model



def _embed(text: str) -> list[float]:
    """Embeds a piece of text into a fixed-length vector."""
    model = _get_embed_model()
    return model.encode(text, convert_to_numpy=True).tolist()


# ── Public API ─────────────────────────────────────────────────────────────────

def store_memory(text: str, topic: str = "general", source: str = "user") -> str:
    """
    Stores a new memory node in the vector graph.

    Args:
        text:   The content to remember (e.g. "I prefer Helm for Kubernetes").
        topic:  Category/topic label for this node (e.g. "kubernetes", "linux").
                Used for topic-scoped focus retrieval.
        source: Origin of the memory — "user", "article", or "system".

    Returns:
        The assigned memory UUID.

    Why: Tagging with a topic creates browsable category nodes in the HNSW graph,
    allowing both semantic search and category-filtered retrieval.
    """
    collection = _get_chroma_collection()
    mem_id = str(uuid.uuid4())
    embedding = _embed(text)
    timestamp = datetime.now().isoformat(timespec="seconds")

    # Normalise topic to lowercase slug
    topic_slug = topic.strip().lower().replace(" ", "_") if topic else "general"

    collection.add(
        ids=[mem_id],
        embeddings=[embedding],
        documents=[text],
        metadatas=[{
            "created_at": timestamp,
            "topic": topic_slug,
            "source": source,
        }],
    )
    logger.info("Stored memory [%s] topic=%s: %.60s", mem_id[:8], topic_slug, text)
    return mem_id


def search_memory(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    topic_filter: str | None = None,
) -> list[dict[str, Any]]:
    """
    Performs an HNSW approximate nearest-neighbour search over stored memories.

    Args:
        query:        Natural-language search string.
        top_k:        Maximum number of results to return before threshold filtering.
        threshold:    Minimum cosine similarity (0–1). Results below this are dropped.
        topic_filter: If set, restricts search to memories with this topic label.

    Returns:
        List of dicts: {"text", "topic", "created_at", "score", "id"}
        Sorted by score descending. Empty list if nothing passes the threshold.

    Why: Threshold filtering is the primary anti-hallucination guard — the model
    is never shown low-confidence results that could cause confabulation.
    """
    collection = _get_chroma_collection()
    if collection.count() == 0:
        return []

    query_embedding = _embed(query)

    kwargs: dict[str, Any] = {
        "query_embeddings": [query_embedding],
        "n_results": min(top_k, collection.count()),
        "include": ["documents", "metadatas", "distances"],
    }

    # Apply topic-level filter if requested (focus mode)
    if topic_filter:
        topic_slug = topic_filter.strip().lower().replace(" ", "_")
        kwargs["where"] = {"topic": topic_slug}

    try:
        results = collection.query(**kwargs)
    except Exception as e:
        logger.error("ChromaDB query failed: %s", e)
        return []

    hits = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]
    ids = results.get("ids", [[]])[0]

    for doc, meta, dist, mem_id in zip(docs, metas, distances, ids):
        # ChromaDB cosine distance: 0 = identical, 2 = opposite.
        # Convert to similarity score in [0, 1].
        score = 1.0 - (dist / 2.0)
        if score < threshold:
            continue
        hits.append({
            "id":         mem_id,
            "text":       doc,
            "topic":      meta.get("topic", "general"),
            "created_at": meta.get("created_at", "unknown"),
            "score":      round(score, 3),
        })

    # Sort by score descending
    hits.sort(key=lambda x: x["score"], reverse=True)
    logger.info("Memory search for '%s': %d results above threshold %.2f", query, len(hits), threshold)
    return hits


def list_memory_topics() -> dict[str, int]:
    """
    Returns a dict mapping topic label → count of memories in that topic.
    Used for 'what have you remembered' queries.
    """
    collection = _get_chroma_collection()
    if collection.count() == 0:
        return {}

    all_results = collection.get(include=["metadatas"])
    topic_counts: dict[str, int] = {}
    for meta in all_results.get("metadatas", []):
        topic = meta.get("topic", "general")
        topic_counts[topic] = topic_counts.get(topic, 0) + 1
    return topic_counts


def delete_memories(query: str, threshold: float = 0.72) -> int:
    """
    Deletes memories that semantically match the query above the threshold.

    Args:
        query:     What to forget (e.g. "my linux setup preferences").
        threshold: Similarity threshold for deletion — slightly lower than search
                   so 'forget X' catches near-matches too.

    Returns:
        Number of memories deleted.

    Why: Slightly lower threshold for deletion gives users a natural "forget" UX —
    you shouldn't need to phrase the delete query identically to the stored text.
    """
    hits = search_memory(query, top_k=20, threshold=threshold)
    if not hits:
        return 0

    collection = _get_chroma_collection()
    ids_to_delete = [h["id"] for h in hits]
    collection.delete(ids=ids_to_delete)
    logger.info("Deleted %d memories matching '%s'", len(ids_to_delete), query)
    return len(ids_to_delete)


def format_for_llm(hits: list[dict[str, Any]], header: str | None = None) -> str:
    """
    Formats retrieved memory nodes into a safe, clearly-labelled block for LLM injection.

    Why: The [Memory] prefix signals to the model that this is retrieved factual
    context, not conversation. The timestamp lets the model reason about recency
    and avoid treating old information as current.
    """
    if not hits:
        return ""

    lines = [header or "[Memory Context — retrieved from long-term memory store]"]
    for h in hits:
        lines.append(f"- ({h['created_at'][:10]}) [{h['topic']}] {h['text']}")
    lines.append(
        "Note: Only use the above memories if directly relevant. "
        "Do not invent or extrapolate beyond what is listed."
    )
    return "\n".join(lines)


def clear_all_memories() -> int:
    """
    Wipes the entire memory collection, resetting Elora's long-term memory completely.
    """
    collection = _get_chroma_collection()
    count = collection.count()
    if count > 0:
        all_results = collection.get()
        all_ids = all_results.get("ids", [])
        if all_ids:
            collection.delete(ids=all_ids)
    logger.info("Cleared all %d memories from vector store", count)
    return count

