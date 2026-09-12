from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ReviewValidation:
    ready: bool
    reviewed_count: int
    clean_count: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


def validate_order_reviews(
    intents: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
) -> ReviewValidation:
    reasons: list[str] = []
    if len(reviews) != len(intents):
        reasons.append("review_count_mismatch")

    intents_by_ticker = {
        str(row.get("ticker") or "").upper(): row
        for row in intents
        if str(row.get("ticker") or "").strip()
    }
    reviews_by_ticker = {
        str(row.get("ticker") or "").upper(): row
        for row in reviews
        if str(row.get("ticker") or "").strip()
    }

    clean_count = 0
    for ticker, intent in sorted(intents_by_ticker.items()):
        review = reviews_by_ticker.get(ticker)
        if review is None:
            reasons.append(f"review_missing:{ticker}")
            continue
        if str(review.get("decision_hash") or "") != str(intent.get("decision_hash") or ""):
            reasons.append(f"review_decision_hash_mismatch:{ticker}")
        data = review.get("data")
        if not isinstance(data, dict):
            reasons.append(f"review_data_missing:{ticker}")
            continue
        checks = data.get("order_checks")
        if checks is None:
            reasons.append(f"order_checks_missing:{ticker}")
            continue
        if not isinstance(checks, dict):
            reasons.append(f"order_checks_invalid:{ticker}")
            continue
        if checks:
            reasons.append(f"order_checks_not_clean:{ticker}")
            continue
        clean_count += 1

    if clean_count != len(intents):
        reasons.append("not_all_reviews_clean")

    return ReviewValidation(
        ready=not reasons,
        reviewed_count=len(reviews),
        clean_count=clean_count,
        reasons=tuple(dict.fromkeys(reasons)),
    )
