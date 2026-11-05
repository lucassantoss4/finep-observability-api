import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

from finep_api.domain import classify
from finep_api.storage import Storage
from finep_api.telemetry import configure, logger, shutdown


def create_app(database_path: str | None = None, telemetry: bool = True):
    storage = Storage(database_path or os.getenv("DATABASE_PATH", "data/finep.db"))
    registry = CollectorRegistry()
    requests = Counter(
        "finep_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=registry
    )
    latency = Histogram(
        "finep_http_request_duration_seconds", "HTTP duration", ["method", "route"], registry=registry
    )
    last_success = Gauge(
        "finep_collection_last_success_timestamp_seconds",
        "Last successful collection, zero means never",
        registry=registry,
    )
    runs = Gauge("finep_collection_runs", "Persisted collection run count", ["result"], registry=registry)
    count = Gauge(
        "finep_opportunities", "Stored opportunities by calculated status", ["status"], registry=registry
    )
    duration = Gauge(
        "finep_collection_last_duration_seconds", "Duration of last attempted collection", registry=registry
    )

    @asynccontextmanager
    async def lifespan(app):
        providers = configure("finep-api") if telemetry else None
        storage.initialize()
        try:
            yield
        finally:
            shutdown(providers)

    app = FastAPI(title="Finep Observability API", version="0.1.0", lifespan=lifespan)
    app.state.storage = storage
    app.state.registry = registry

    @app.middleware("http")
    async def observe(request: Request, call_next):
        started = time.monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            if route != "/metrics":
                requests.labels(request.method, route, str(status)).inc()
                latency.labels(request.method, route).observe(time.monotonic() - started)
                logger.info("request_completed method=%s route=%s status=%s", request.method, route, status)

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, exc):
        logger.error("database_unavailable error_type=%s", type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": "Storage unavailable"})

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        storage.ready()
        return {"status": "ready"}

    @app.get("/opportunities")
    def opportunities(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        items, checked = storage.snapshot()
        now = datetime.now(UTC)
        today = now.astimezone(ZoneInfo("America/Sao_Paulo")).date()
        data = []
        for item in items[offset : offset + limit]:
            status, reason = classify(item, today)
            data.append(
                {
                    **item.model_dump(mode="json"),
                    "status": status,
                    "status_reason": reason,
                    "last_checked_at": checked,
                }
            )
        stale = checked is None or (now - datetime.fromisoformat(checked)).total_seconds() > 172800
        return {
            "items": data,
            "total": len(items),
            "limit": limit,
            "offset": offset,
            "last_success_at": checked,
            "data_stale": stale,
            "coverage": "Summary page only; not the complete Finep catalogue",
        }

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        items, checked = storage.snapshot()
        last_success.set(datetime.fromisoformat(checked).timestamp() if checked else 0)
        stats, last = storage.collection_stats()
        runs.labels("success").set(stats.get("1", 0))
        runs.labels("failure").set(stats.get("0", 0))
        duration.set(last["duration"] if last else 0)
        today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
        statuses = [classify(item, today)[0] for item in items]
        for status in ("aberto", "encerrado", "nao_confirmado"):
            count.labels(status).set(statuses.count(status))
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    if telemetry:
        FastAPIInstrumentor.instrument_app(app, excluded_urls="metrics,health/live,health/ready")
    return app
