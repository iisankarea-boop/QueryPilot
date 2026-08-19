from querypilot.application.support_seed import (
    DOCUMENT_COLLECTIONS,
    EDGE_COLLECTIONS,
    build_support_seed,
)


def test_support_seed_is_repeatable_and_referentially_complete() -> None:
    first = build_support_seed()
    second = build_support_seed()

    assert first == second
    assert {name: len(rows) for name, rows in first.items()} == {
        "companies": 80,
        "specialists": 24,
        "topics": 12,
        "cases": 1_500,
        "case_events": 4_500,
        "raised_by": 1_500,
        "owned_by": 1_500,
        "classified_as": 1_500,
        "has_event": 4_500,
    }

    document_ids = {
        f"{collection}/{row['_key']}"
        for collection in DOCUMENT_COLLECTIONS
        for row in first[collection]
    }
    edge_endpoints = {
        endpoint
        for collection in EDGE_COLLECTIONS
        for row in first[collection]
        for endpoint in (row["_from"], row["_to"])
    }
    assert edge_endpoints <= document_ids


def test_support_schema_uses_domain_specific_fields() -> None:
    seed = build_support_seed()

    assert set(seed["cases"][0]) == {
        "_key",
        "headline",
        "case_state",
        "urgency_band",
        "contact_channel",
        "opened_on",
        "first_response_minutes",
        "resolve_minutes",
        "customer_score",
        "sla_breached",
    }
    assert set(seed["companies"][0]) >= {
        "legal_name",
        "industry_group",
        "contract_tier",
        "home_region",
    }
    assert set(seed["case_events"][0]) >= {
        "event_kind",
        "happened_on",
        "actor_kind",
        "work_minutes",
    }
