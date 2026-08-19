from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from querypilot.domain.catalog import ContextPack
from querypilot.domain.models import AqlCandidate
from querypilot.domain.query_ir import StructuredQueryPlan


class AskCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=4_000)


class AnswerSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1, max_length=4_000)


@dataclass(frozen=True, slots=True)
class QueryResult:
    rows: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class PlanPrompt:
    question: str
    context: ContextPack
    validation_feedback: tuple[str, ...] = ()
    rejected_plan: StructuredQueryPlan | None = None


@dataclass(frozen=True, slots=True)
class SummaryPrompt:
    question: str
    plan: StructuredQueryPlan
    candidate: AqlCandidate
    result: QueryResult


@dataclass(frozen=True, slots=True)
class RunEvent:
    run_id: str
    seq: int
    type: str
    payload: dict[str, Any]
    occurred_at: datetime

    @classmethod
    def now(
        cls,
        run_id: str,
        seq: int,
        type: str,
        payload: dict[str, Any],
    ) -> "RunEvent":
        return cls(
            run_id=run_id,
            seq=seq,
            type=type,
            payload=payload,
            occurred_at=datetime.now(UTC),
        )
