"""Grouping gap questions by meaning, without an LLM: the app's embedder, then
deterministic average-linkage clustering on cosine similarity."""

from collections import OrderedDict

import numpy as np

from app.core.config import get_settings
from app.embeddings import get_embedder

# Average cosine similarity at which questions count as the same gap. Neural embedders
# score paraphrases high (bge-small ~0.8–0.9, Voyage ~0.7–0.85); the hash embedder is
# lexical, so paraphrases that share their key words land around 0.5. GAP_SIMILARITY
# overrides.
DEFAULT_SIMILARITY = {"fastembed": 0.8, "voyage": 0.7, "hash": 0.5}
CACHE_SIZE = 5000  # question embeddings kept in memory, least recently used dropped
EMBED_BATCH = 128
MAX_EMBED_CHARS = 1000

_cache: OrderedDict[tuple[str, str], np.ndarray] = OrderedDict()


def similarity_threshold() -> float:
    s = get_settings()
    if s.gap_similarity is not None:
        return s.gap_similarity
    return DEFAULT_SIMILARITY.get(s.embedding_provider, 0.75)


async def embed(texts: list[str]) -> np.ndarray:
    """Unit vectors for `texts`, one row each. An embedding depends only on the model and
    the text, so they're cached in-process: recomputing gaps embeds only new questions."""
    embedder = get_embedder()
    model = f"{embedder.name}:{getattr(embedder, 'model_name', '')}:{embedder.dim}"
    found: dict[str, np.ndarray] = {}
    todo: list[str] = []
    for text in dict.fromkeys(texts):
        if (vec := _cache.get((model, text))) is not None:
            _cache.move_to_end((model, text))
            found[text] = vec
        else:
            todo.append(text)
    for i in range(0, len(todo), EMBED_BATCH):
        batch = todo[i : i + EMBED_BATCH]
        vectors = await embedder.embed_documents([t[:MAX_EMBED_CHARS] for t in batch])
        for text, values in zip(batch, vectors, strict=True):
            vec = np.asarray(values, dtype=np.float32)
            norm = float(np.linalg.norm(vec))
            found[text] = vec / norm if norm else vec
            _cache[(model, text)] = found[text]
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    if not texts:
        return np.zeros((0, embedder.dim), dtype=np.float32)
    return np.stack([found[t] for t in texts])


def average_linkage(vectors: np.ndarray, weights: np.ndarray, threshold: float) -> list[list[int]]:
    """Agglomerative clustering with weighted average linkage: repeatedly merge the two
    groups whose members are most similar on average, until no two groups reach
    `threshold`. `weights` count repeats of one question (five people asking the same
    thing weigh five), which equals clustering every repeat. Deterministic: ties merge
    the lowest indices first. Returns groups of row indices, in first-member order."""
    n = len(vectors)
    if n == 0:
        return []
    sim = (vectors @ vectors.T).astype(np.float64)
    np.fill_diagonal(sim, -np.inf)
    size = weights.astype(np.float64).copy()
    groups = [[i] for i in range(n)]
    while True:
        i, j = divmod(int(np.argmax(sim)), n)
        if sim[i, j] < threshold:
            break
        i, j = min(i, j), max(i, j)
        # Lance–Williams: the merged group's average similarity to every other group.
        merged = (size[i] * sim[i] + size[j] * sim[j]) / (size[i] + size[j])
        sim[i, :] = merged
        sim[:, i] = merged
        sim[i, i] = -np.inf
        sim[j, :] = -np.inf
        sim[:, j] = -np.inf
        size[i] += size[j]
        groups[i] += groups[j]
        groups[j] = []
    return sorted((sorted(g) for g in groups if g), key=lambda g: g[0])


def most_central(members: list[int], vectors: np.ndarray, weights: np.ndarray) -> int:
    """The member most similar to its group (weighted, itself included, so a question
    asked often wins a tie); the lowest index on equal scores."""
    if len(members) == 1:
        return members[0]
    sub = vectors[members]
    scores = (sub @ sub.T) @ weights[members].astype(np.float64)
    return members[int(np.argmax(scores))]


def centroid(members: list[int], vectors: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Weighted mean of the members' unit vectors: its dot product with a unit vector is
    that vector's average similarity to the group, the quantity clustering thresholds."""
    w = weights[members].astype(np.float64)
    return (vectors[members].astype(np.float64) * w[:, None]).sum(axis=0) / w.sum()
