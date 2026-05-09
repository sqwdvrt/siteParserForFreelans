from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

from ai_service.util.fallback_metrics import increment_counter

logger = logging.getLogger(__name__)

ItemT = TypeVar("ItemT")

_ALL_FILTERED_METRIC = "siteparser_rerank_all_filtered_total"
_FALLBACK_METRIC = "siteparser_rerank_fallback_total"


@dataclass(frozen=True)
class RerankPolicyDecision(Generic[ItemT]):
    selected: list[ItemT]
    ranked: list[ItemT]
    candidate_count: int
    selected_count: int
    best_score_before_filter: float
    min_score_before_filter: float
    mean_score_before_filter: float
    threshold: float
    used_fallback: bool
    all_filtered: bool


def apply_rerank_policy(
    items: list[ItemT],
    *,
    score_getter: Callable[[ItemT], float],
    threshold: float,
    top_k: int,
    fallback_enabled: bool,
    pipeline: str,
    entity_name: str,
    entity_id: int | str,
) -> RerankPolicyDecision[ItemT]:
    normalized_top_k = max(0, int(top_k))
    if not items or normalized_top_k == 0:
        return RerankPolicyDecision(
            selected=[],
            ranked=[],
            candidate_count=len(items),
            selected_count=0,
            best_score_before_filter=0.0,
            min_score_before_filter=0.0,
            mean_score_before_filter=0.0,
            threshold=threshold,
            used_fallback=False,
            all_filtered=False,
        )

    scored = sorted(
        ((float(score_getter(item)), item) for item in items),
        key=lambda pair: pair[0],
        reverse=True,
    )
    ranked = [item for _, item in scored]
    scores = [score for score, _ in scored]
    best_score = scores[0]
    min_score = scores[-1]
    mean_score = sum(scores) / len(scores)

    logger.info(
        "%s_id=%s: rerank scores before filter count=%d max=%.3f min=%.3f mean=%.3f threshold=%.2f",
        entity_name,
        entity_id,
        len(scores),
        best_score,
        min_score,
        mean_score,
        threshold,
    )

    selected = [item for score, item in scored if score >= threshold][:normalized_top_k]
    all_filtered = bool(scored) and not selected
    used_fallback = False

    if all_filtered:
        increment_counter(
            _ALL_FILTERED_METRIC,
            "Times reranker filtered 100% of candidates before fallback handling.",
            labels={"pipeline": pipeline},
        )
        if fallback_enabled:
            selected = [ranked[0]]
            used_fallback = True
            increment_counter(
                _FALLBACK_METRIC,
                "Times rerank policy fell back to the top candidate after filtering all candidates.",
                labels={"pipeline": pipeline},
            )
            logger.warning(
                "%s_id=%s: reranker filtered 100%% of candidates (best_score=%.3f < %.2f), "
                "falling back to top-1 fallback=all_filtered filtered=%d selected=1",
                entity_name,
                entity_id,
                best_score,
                threshold,
                len(scores),
            )
        else:
            logger.warning(
                "%s_id=%s: reranker filtered 100%% of candidates (best_score=%.3f < %.2f), "
                "fallback disabled fallback=disabled filtered=%d selected=0",
                entity_name,
                entity_id,
                best_score,
                threshold,
                len(scores),
            )

    return RerankPolicyDecision(
        selected=selected,
        ranked=ranked,
        candidate_count=len(scores),
        selected_count=len(selected),
        best_score_before_filter=best_score,
        min_score_before_filter=min_score,
        mean_score_before_filter=mean_score,
        threshold=threshold,
        used_fallback=used_fallback,
        all_filtered=all_filtered,
    )
