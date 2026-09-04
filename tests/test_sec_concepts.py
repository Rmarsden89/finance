import pandas as pd

from finance.data.sec_concepts import map_canonical_facts


def test_maps_known_tags_and_preserves_source_tag() -> None:
    facts = pd.DataFrame(
        [
            {"adsh": "1", "tag": "Revenues", "value": 100},
            {"adsh": "1", "tag": "NetIncomeLoss", "value": 10},
            {"adsh": "1", "tag": "RandomCustomTag", "value": 7},
        ]
    )

    mapped = map_canonical_facts(facts)

    assert list(mapped["concept"]) == ["revenue", "net_income"]
    assert list(mapped["source_tag"]) == ["Revenues", "NetIncomeLoss"]
