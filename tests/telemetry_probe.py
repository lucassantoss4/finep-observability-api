"""Exercise the real OTLP HTTP exporters against a local recording receiver."""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opentelemetry import trace
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from finep_api.telemetry import configure, logger, shutdown

received = {}


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = self.rfile.read(int(self.headers["Content-Length"]))
        received.setdefault(self.path, []).append(payload)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"http://127.0.0.1:{server.server_port}"
os.environ["OTEL_TRACES_SAMPLER"] = "always_on"
providers = configure("finep-probe")
try:
    with trace.get_tracer("probe").start_as_current_span("probe.request"):
        logger.info("probe_event")
    for provider in providers:
        if provider:
            provider.force_flush()
finally:
    shutdown(providers)
    server.shutdown()
traces = ExportTraceServiceRequest.FromString(received["/v1/traces"][0])
logs = ExportLogsServiceRequest.FromString(received["/v1/logs"][0])
span = traces.resource_spans[0].scope_spans[0].spans[0]
record = logs.resource_logs[0].scope_logs[0].log_records[0]
assert record.body.string_value == "probe_event"
assert record.trace_id == span.trace_id
assert record.span_id == span.span_id
assert span.name == "probe.request"
print("OTLP traces and logs received with matching trace_id and span_id")
