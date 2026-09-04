from __future__ import annotations

from collections import defaultdict

import pandas as pd


CANONICAL_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
    ),
    "net_income": (
        "NetIncomeLoss",
        "ProfitLoss",
    ),
    "operating_income": (
        "OperatingIncomeLoss",
    ),
    "total_assets": (
        "Assets",
    ),
    "total_liabilities": (
        "Liabilities",
    ),
    "shareholders_equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "cash": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capital_expenditures": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForAdditionsToPropertyPlantAndEquipment",
    ),
    "shares_outstanding": (
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
    ),
}


def tag_to_concept_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    duplicates: dict[str, list[str]] = defaultdict(list)

    for concept, tags in CANONICAL_TAGS.items():
        for tag in tags:
            if tag in mapping and mapping[tag] != concept:
                duplicates[tag].extend([mapping[tag], concept])
            mapping[tag] = concept

    if duplicates:
        raise ValueError(f"Ambiguous canonical SEC tag mappings: {dict(duplicates)}")

    return mapping


def map_canonical_facts(facts: pd.DataFrame) -> pd.DataFrame:
    """Map selected SEC tags into conservative v0.1 canonical concepts.

    The source SEC tag is preserved. Facts whose tags are outside the curated
    mapping are excluded rather than guessed.
    """

    required = {"tag", "value", "adsh"}
    missing = required - set(facts.columns)
    if missing:
        raise ValueError(
            "Facts frame missing required columns: "
            + ", ".join(sorted(missing))
        )

    mapping = tag_to_concept_map()
    mapped = facts.loc[facts["tag"].isin(mapping)].copy()
    mapped["concept"] = mapped["tag"].map(mapping)
    mapped["source_tag"] = mapped["tag"]
    return mapped
