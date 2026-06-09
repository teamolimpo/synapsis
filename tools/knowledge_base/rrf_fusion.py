"""Reciprocal Rank Fusion for combining multiple search signals.

Supports both legacy 2-signal mode (BM25 + embedding) and N-signal mode
via a ``dict[str, list]`` input.
"""

from __future__ import annotations

from typing import Any

RRF_K_DEFAULT = 60


def fuse_rrf(
    signals_or_bm25: dict[str, list[dict[str, Any]]] | list[dict[str, Any]],
    embedding_or_none: list[dict[str, Any]] | None = None,
    k: int = RRF_K_DEFAULT,
    weights: dict[str, float] | None = None,
    weight_bm25: float = 1.0,
    weight_embed: float = 1.0,
) -> list[dict[str, Any]]:
    """Fuse ranked result lists using Reciprocal Rank Fusion.

    Supports two calling conventions:

    **N-signal mode** (new)::

        fuse_rrf({"bm25": [...], "embedding": [...], "entity": [...]}, k=60)

    **Legacy 2-signal mode** (backward-compatible)::

        fuse_rrf(bm25_results, embedding_results, k=60, weight_bm25=1.0, weight_embed=1.0)

    Args:
        signals_or_bm25:
            Either a dict mapping signal names to ranked result lists, or
            the BM25 result list (legacy mode).
        embedding_or_none:
            Embedding result list (legacy mode). ``None`` when using dict.
        k:
            RRF constant (default 60). Higher values give more weight to
            lower-ranked items.
        weights:
            Per-signal weights (N-signal mode only). Keys must match those
            in *signals_or_bm25*. Overrides legacy *weight_bm25*/*weight_embed*.
        weight_bm25:
            Weight for BM25 signal (legacy mode, default 1.0).
        weight_embed:
            Weight for embedding signal (legacy mode, default 1.0).

    Returns:
        List of result dicts sorted by RRF score descending.
        Each result gets an ``_rrf_score`` key.
    """
    # --- N-signal dict mode ---
    if isinstance(signals_or_bm25, dict):
        return _fuse_n_signals(signals_or_bm25, k=k, weights=weights)

    # --- Legacy 2-signal mode ---
    if embedding_or_none is None:
        # Only one signal provided
        return list(signals_or_bm25)

    return _fuse_n_signals(
        {
            "bm25": signals_or_bm25,
            "embedding": embedding_or_none,
        },
        k=k,
        weights={"bm25": weight_bm25, "embedding": weight_embed},
    )


def _fuse_n_signals(
    signals: dict[str, list[dict[str, Any]]],
    k: int = RRF_K_DEFAULT,
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Fuse N signals using Reciprocal Rank Fusion.

    Args:
        signals: Dict of ``signal_name → list[dict]``. Each dict must have
            an ``"id"`` key.
        k: RRF constant.
        weights: Per-signal weights. Signals not present default to 1.0.

    Returns:
        Sorted list of fused results with ``_rrf_score`` key.
    """
    scores: dict[str, float] = {}
    doc_map: dict[str, dict[str, Any]] = {}

    for signal_name, results in signals.items():
        w = (weights or {}).get(signal_name, 1.0)
        for rank, doc in enumerate(results, start=1):
            doc_id = doc["id"]
            scores[doc_id] = scores.get(doc_id, 0.0) + w * (1.0 / (k + rank))
            if doc_id not in doc_map:
                doc_map[doc_id] = dict(doc)

    fused: list[dict[str, Any]] = []
    for doc_id, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        entry = doc_map[doc_id]
        entry["_rrf_score"] = score
        fused.append(entry)

    return fused
