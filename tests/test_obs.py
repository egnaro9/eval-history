"""Tests for the observability layer: JSON logs, request id, and /metrics."""
from __future__ import annotations

import json
import logging

from evalhistory.obs import REQUEST_ID_HEADER, JsonFormatter, request_id_var


def test_json_formatter_emits_one_object_with_extras():
    rec = logging.makeLogRecord({"name": "evalhistory", "levelname": "INFO",
                                 "levelno": logging.INFO, "msg": "request"})
    rec.status = 201
    rec.route = "/runs"
    obj = json.loads(JsonFormatter().format(rec))   # one valid JSON object
    assert obj["msg"] == "request"
    assert obj["level"] == "INFO"
    assert obj["status"] == 201                      # extra field surfaced
    assert obj["route"] == "/runs"
    assert "request_id" in obj and "ts" in obj


def test_json_formatter_includes_current_request_id():
    token = request_id_var.set("abc123")
    try:
        obj = json.loads(JsonFormatter().format(logging.makeLogRecord({"msg": "x"})))
        assert obj["request_id"] == "abc123"
    finally:
        request_id_var.reset(token)


def test_response_carries_a_generated_request_id(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.headers.get(REQUEST_ID_HEADER)          # minted when the caller sends none


def test_request_id_is_echoed_when_supplied(client):
    r = client.get("/health", headers={REQUEST_ID_HEADER: "trace-42"})
    assert r.headers.get(REQUEST_ID_HEADER) == "trace-42"


def test_metrics_endpoint_exposes_prometheus_text(client):
    client.get("/health")                            # produce at least one sample
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert "http_requests_total" in r.text
    assert "http_request_duration_seconds" in r.text


def test_metrics_labeled_by_route_template_not_raw_id(client):
    # A raw id in the path must NOT become a metric label — the cardinality bomb
    # this whole design exists to prevent.
    client.get("/runs/deadbeefdeadbeefdeadbeefdeadbeef")   # 404, but the route matched
    body = client.get("/metrics").text
    assert 'route="/runs/{run_id}"' in body
    assert "deadbeef" not in body


def test_metrics_scrape_is_not_self_instrumented(client):
    # /metrics passes through the middleware untouched, so a scrape never mints a
    # sample for itself.
    client.get("/metrics")
    body = client.get("/metrics").text
    assert 'route="/metrics"' not in body


class _Capture(logging.Handler):
    """Collects LogRecords straight off the 'evalhistory' logger — independent of
    root handlers and pytest's caplog, both of which other tests reconfigure."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_middleware_logs_a_structured_request_line(client):
    cap = _Capture()
    logger = logging.getLogger("evalhistory")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(cap)
    try:
        client.get("/health")
    finally:
        logger.removeHandler(cap)
        logger.setLevel(old_level)
    line = next(r for r in cap.records if r.getMessage() == "request")
    assert line.method == "GET"
    assert line.path == "/health"
    assert line.route == "/health"
    assert line.status == 200
    assert isinstance(line.duration_ms, float)
