import json
import logging
import os
import sys
from datetime import UTC, datetime

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger("finep")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        context = trace.get_current_span().get_span_context()
        data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "trace_id": format(context.trace_id, "032x"),
            "span_id": format(context.span_id, "016x"),
        }
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def configure(service: str):
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return None
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(JsonFormatter())
    logger.addHandler(stream)
    resource = Resource.create({"service.name": service})
    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument()
    SQLite3Instrumentor().instrument()
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return provider, None
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces", timeout=5))
    )
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.instrumentation.logging.handler import LoggingHandler
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

    logs = LoggerProvider(resource=resource)
    logs.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{endpoint.rstrip('/')}/v1/logs", timeout=5))
    )
    logger.addHandler(LoggingHandler(logger_provider=logs))
    return provider, logs


def shutdown(providers):
    if providers:
        for provider in providers:
            if provider:
                provider.shutdown()
