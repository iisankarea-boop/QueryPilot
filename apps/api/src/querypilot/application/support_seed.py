import random
from datetime import UTC, datetime, timedelta
from typing import Any

DOCUMENT_COLLECTIONS = (
    "companies",
    "specialists",
    "topics",
    "cases",
    "case_events",
)
EDGE_COLLECTIONS = (
    "raised_by",
    "owned_by",
    "classified_as",
    "has_event",
)


def build_support_seed(seed: int = 20260819) -> dict[str, list[dict[str, Any]]]:
    randomizer = random.Random(seed)
    companies = _companies()
    specialists = _specialists()
    topics = _topics()
    cases: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    raised_by: list[dict[str, Any]] = []
    owned_by: list[dict[str, Any]] = []
    classified_as: list[dict[str, Any]] = []
    has_event: list[dict[str, Any]] = []
    start = datetime(2024, 1, 1, 8, tzinfo=UTC)

    for index in range(1, 1_501):
        case_key = f"case-{index:05d}"
        company_key = f"company-{((index * 17) % len(companies)) + 1:03d}"
        specialist_key = f"specialist-{((index * 7) % len(specialists)) + 1:03d}"
        topic_key = f"topic-{((index * 5) % len(topics)) + 1:02d}"
        opened_on = start + timedelta(hours=index * 11)
        urgency = ("low", "normal", "high", "critical")[index % 4]
        channel = ("portal", "email", "phone", "chat")[index % 4]
        state = ("resolved", "resolved", "resolved", "waiting", "active")[index % 5]
        first_response_minutes = 8 + (index * 13) % 230
        resolve_minutes = None if state != "resolved" else 45 + (index * 29) % 4_300
        customer_score = (
            None if state != "resolved" else 1 + ((index * 11 + index // 7) % 5)
        )
        sla_target = {"low": 720, "normal": 360, "high": 180, "critical": 60}[urgency]
        cases.append(
            {
                "_key": case_key,
                "headline": f"Support request {index:05d}",
                "case_state": state,
                "urgency_band": urgency,
                "contact_channel": channel,
                "opened_on": _timestamp(opened_on),
                "first_response_minutes": first_response_minutes,
                "resolve_minutes": resolve_minutes,
                "customer_score": customer_score,
                "sla_breached": first_response_minutes > sla_target,
            }
        )
        raised_by.append(
            {
                "_key": case_key,
                "_from": f"companies/{company_key}",
                "_to": f"cases/{case_key}",
                "intake_channel": channel,
            }
        )
        owned_by.append(
            {
                "_key": case_key,
                "_from": f"cases/{case_key}",
                "_to": f"specialists/{specialist_key}",
                "assigned_on": _timestamp(opened_on + timedelta(minutes=3 + index % 40)),
                "primary_owner": True,
            }
        )
        classified_as.append(
            {
                "_key": case_key,
                "_from": f"cases/{case_key}",
                "_to": f"topics/{topic_key}",
                "confidence": round(0.7 + (index % 30) / 100, 2),
                "classification_source": "rules" if index % 3 else "specialist",
            }
        )

        event_kinds = ("received", "investigated", "resolved" if state == "resolved" else "updated")
        for sequence, event_kind in enumerate(event_kinds, start=1):
            event_key = f"event-{index:05d}-{sequence}"
            happened_on = opened_on + timedelta(minutes=sequence * (12 + index % 90))
            work_minutes = 4 + (index * sequence * 7) % 120
            events.append(
                {
                    "_key": event_key,
                    "event_kind": event_kind,
                    "happened_on": _timestamp(happened_on),
                    "actor_kind": "customer" if sequence == 1 else "specialist",
                    "work_minutes": work_minutes,
                    "note_length": 40 + randomizer.randrange(20, 900),
                }
            )
            has_event.append(
                {
                    "_key": event_key,
                    "_from": f"cases/{case_key}",
                    "_to": f"case_events/{event_key}",
                    "sequence_no": sequence,
                }
            )

    return {
        "companies": companies,
        "specialists": specialists,
        "topics": topics,
        "cases": cases,
        "case_events": events,
        "raised_by": raised_by,
        "owned_by": owned_by,
        "classified_as": classified_as,
        "has_event": has_event,
    }


def _companies() -> list[dict[str, Any]]:
    industries = ("healthcare", "logistics", "education", "media", "manufacturing")
    tiers = ("starter", "growth", "enterprise")
    regions = ("north", "east", "south", "west")
    return [
        {
            "_key": f"company-{index:03d}",
            "legal_name": f"Company {index:03d}",
            "industry_group": industries[index % len(industries)],
            "contract_tier": tiers[index % len(tiers)],
            "home_region": regions[index % len(regions)],
            "employee_band": ("1-50", "51-200", "201-1000", "1000+")[index % 4],
            "onboarded_on": _timestamp(
                datetime(2022, 1, 1, tzinfo=UTC) + timedelta(days=index * 9)
            ),
        }
        for index in range(1, 81)
    ]


def _specialists() -> list[dict[str, Any]]:
    return [
        {
            "_key": f"specialist-{index:03d}",
            "display_label": f"Specialist {index:03d}",
            "squad_name": ("platform", "billing", "identity", "integrations")[index % 4],
            "seniority_band": ("associate", "regular", "senior")[index % 3],
            "enabled_flag": index % 11 != 0,
            "hired_on": _timestamp(datetime(2021, 6, 1, tzinfo=UTC) + timedelta(days=index * 17)),
        }
        for index in range(1, 25)
    ]


def _topics() -> list[dict[str, Any]]:
    return [
        {
            "_key": f"topic-{index:02d}",
            "topic_label": (
                "Authentication",
                "Invoices",
                "Webhooks",
                "Exports",
                "Permissions",
                "Latency",
            )[(index - 1) % 6],
            "domain_group": ("access", "finance", "integration")[index % 3],
            "complexity_band": ("basic", "moderate", "advanced")[index % 3],
        }
        for index in range(1, 13)
    ]


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
