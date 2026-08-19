import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import lru_cache
from pathlib import Path
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from querypilot.adapters.readiness import build_readiness_checker
from querypilot.application.query_agent import QueryAgent
from querypilot.application.readiness import ReadinessChecker
from querypilot.application.source_manager import SourceNotFoundError
from querypilot.bootstrap import AppContainer, build_container
from querypilot.config import get_settings
from querypilot.domain.models import QueryRejected
from querypilot.domain.query import AskCommand, RunEvent
from querypilot.domain.source import SourceConnection, SourceInfo
from querypilot.transport.rate_limit import SlidingWindowRateLimiter, client_identifier

logger = logging.getLogger(__name__)


class RunRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    question: str = Field(min_length=1, max_length=4_000)
    source_id: str = Field(default="commerce", min_length=1, max_length=128)
    thread_id: str | None = Field(default=None, max_length=128)


@lru_cache(maxsize=1)
def _container() -> AppContainer:
    return build_container(get_settings())


@lru_cache(maxsize=1)
def _readiness() -> ReadinessChecker:
    return build_readiness_checker(get_settings())


@lru_cache(maxsize=1)
def _rate_limiter() -> SlidingWindowRateLimiter:
    settings = get_settings()
    return SlidingWindowRateLimiter(
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )


def create_app() -> FastAPI:
    app = FastAPI(title="QueryPilot", version="0.1.0")

    @app.middleware("http")
    async def production_controls(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = str(uuid4())
        started_at = monotonic()
        if request.method == "POST" and request.url.path in {
            "/api/v1/runs:stream",
            "/api/v1/sources",
        }:
            settings = get_settings()
            direct_host = request.client.host if request.client else None
            client_id = client_identifier(
                direct_host,
                request.headers.get("x-forwarded-for"),
                trust_proxy_headers=settings.trust_proxy_headers,
            )
            retry_after = _rate_limiter().consume(client_id)
            if retry_after is not None:
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": "请求过于频繁，请稍后再试。"},
                    headers={"Retry-After": str(retry_after), "X-Request-ID": request_id},
                )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": round((monotonic() - started_at) * 1_000, 2),
            },
        )
        return response

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        report = await _readiness().check()
        return JSONResponse(
            status_code=status.HTTP_200_OK if report.ready else status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "ready" if report.ready else "degraded",
                "dependencies": report.dependencies,
            },
        )

    @app.get("/api/v1/sources", response_model=list[SourceInfo])
    async def list_sources() -> list[SourceInfo]:
        return list(_container().sources.list_sources())

    @app.post(
        "/api/v1/sources",
        response_model=SourceInfo,
        status_code=status.HTTP_201_CREATED,
    )
    async def onboard_source(connection: SourceConnection) -> SourceInfo:
        try:
            return await _container().sources.onboard(connection)
        except Exception as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "无法连接或扫描 ArangoDB，请检查地址、数据库、只读账号权限和网络。"
                ),
            ) from error

    @app.post("/api/v1/runs:stream", response_class=StreamingResponse)
    async def stream_run(request: RunRequest) -> StreamingResponse:
        try:
            agent = _container().sources.agent_for(request.source_id)
        except SourceNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error
        run_id = str(uuid4())
        command = AskCommand(
            run_id=run_id,
            thread_id=request.thread_id or str(uuid4()),
            source_id=request.source_id,
            question=request.question,
        )
        return StreamingResponse(
            _sse_events(agent, command),
            media_type="text/event-stream",
            headers={"X-QueryPilot-Run-Id": run_id, "Cache-Control": "no-cache"},
        )

    web_dist = Path("apps/web/dist")
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")

    return app


async def _sse_events(
    agent: QueryAgent,
    command: AskCommand,
) -> AsyncIterator[str]:
    try:
        async for event in agent.stream(command):
            yield _encode_sse(event)
    except QueryRejected as error:
        payload = {
            "run_id": command.run_id,
            "type": "failed",
            "payload": {"code": error.code, "message": str(error)},
        }
        yield f"event: failed\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    except Exception:
        logger.exception(
            "Unhandled query run failure",
            extra={"run_id": command.run_id},
        )
        payload = {
            "run_id": command.run_id,
            "type": "failed",
            "payload": {
                "code": "internal_error",
                "message": "Query execution failed.",
            },
        }
        yield f"event: failed\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _encode_sse(event: RunEvent) -> str:
    payload = {
        "run_id": event.run_id,
        "seq": event.seq,
        "type": event.type,
        "payload": event.payload,
        "occurred_at": event.occurred_at.isoformat(),
    }
    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n"


app = create_app()
