"""Observability: structured logs, request timing, and Prometheus metrics.

The service had one `print()` and no request logging — enough for a demo, thin
for something you operate. This adds the three things you actually reach for
when a deployed service misbehaves:

  - a **structured line per request** on stdout (so a log search is a query, not
    a grep) — one JSON object per line, the shape every hosted pipeline (Render,
    CloudWatch, Loki) ingests without a parser;
  - a **propagated request id** (`X-Request-ID`, echoed on the response), so one
    caller's path is traceable across the logs and back to the client;
  - **/metrics** for Prometheus, so error rate and latency are numbers.

Metrics are labeled by the matched **route template** (`/runs/{run_id}`), never
the raw path (`/runs/abc123`). Labeling by raw path is the classic cardinality
bomb — a million ids become a million time series; the template collapses them
to one. This is a pure-ASGI middleware rather than a BaseHTTPMiddleware because
it shares the request `scope` dict with the router in place, so the route the
router matched is actually visible here after the call.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextvars import ContextVar

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
METRICS_PATH = "/metrics"

# The current request's id, so any log call inside a handler carries it without
# threading an argument through every function signature.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

logger = logging.getLogger("evalhistory")


# --- structured logging ----------------------------------------------------

class JsonFormatter(logging.Formatter):
    """One JSON object per line: standard fields plus whatever `extra=` carried."""

    # Attribute names already present on a bare LogRecord; anything outside this
    # set was added by a caller's `extra=` and should be surfaced.
    _RESERVED = set(vars(logging.makeLogRecord({}))) | {"taskName"}

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for key, val in record.__dict__.items():
            if key not in self._RESERVED and key not in payload:
                payload[key] = val
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Point the root logger at stdout with the JSON formatter.

    Idempotent: it replaces its own named handler rather than stacking a new one
    on every call. `create_app` runs once per test, and a duplicated handler
    means every log line printed twice. Only our handler is touched, so pytest's
    caplog handler (and anything else on root) survives.
    """
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.set_name("evalhistory-json")

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [h for h in root.handlers if h.get_name() != "evalhistory-json"]
    root.addHandler(handler)


# --- metrics ---------------------------------------------------------------

REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests, by method, matched route template, and status code.",
    ["method", "route", "status"],
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds, by method and matched route template.",
    ["method", "route"],
)


def _route_template(scope: Scope) -> str:
    """The matched route (`/runs/{run_id}`), not the raw path (`/runs/abc123`).

    A request that matched no route (a routing 404) has no template, so it is
    bucketed as 'unmatched' rather than leaking the path someone probed.
    """
    route = scope.get("route")
    return getattr(route, "path", None) or "unmatched"


class RequestObservability:
    """Times every HTTP request, records a metric + a JSON log line, and stamps
    a request id on the way out. Scrapes of `/metrics` pass through untouched so
    a 15-second scrape loop never dominates the logs or the latency histogram."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == METRICS_PATH:
            await self.app(scope, receive, send)
            return

        request_id = Request(scope).headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        status = 500  # if the app raises before responding, that's what we record

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            route = _route_template(scope)  # set in-place by the router by now
            REQUESTS.labels(scope["method"], route, str(status)).inc()
            LATENCY.labels(scope["method"], route).observe(elapsed)
            logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "route": route,
                    "status": status,
                    "duration_ms": round(elapsed * 1000, 1),
                },
            )
            request_id_var.reset(token)


def metrics_response() -> Response:
    """The Prometheus scrape endpoint's body."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
