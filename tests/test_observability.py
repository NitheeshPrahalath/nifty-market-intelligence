"""Observability unit tests (Phase 10)."""

from __future__ import annotations

import json
import logging

from nmi.core import logging as nmi_logging
from nmi.core.metrics import REGISTRY


def test_json_formatter_includes_request_id_and_extra_fields():
    logger = logging.getLogger("test.nmi")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    formatter = nmi_logging.JsonFormatter()
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    token = nmi_logging.bind_request_id("req-123456")
    try:
        logger.info("hello", extra={"fields": {"index": "NIFTY_50", "count": 3}})
    finally:
        nmi_logging.reset_request_id(token)
        logger.removeHandler(handler)

    record = json.loads(stream.getvalue())
    assert record["message"] == "hello"
    assert record["logger"] == "test.nmi"
    assert record["request_id"] == "req-123456"
    assert record["index"] == "NIFTY_50"
    assert record["count"] == 3


def test_bind_get_reset_request_id_round_trip():
    token = nmi_logging.bind_request_id("abc1234567890")
    try:
        assert nmi_logging.get_request_id() == "abc1234567890"
    finally:
        nmi_logging.reset_request_id(token)
    assert nmi_logging.get_request_id() is None


def test_setup_logging_switches_to_json_format():
    nmi_logging.setup_logging(level="DEBUG", fmt="json")
    root = logging.getLogger()
    handler = root.handlers[0]
    assert isinstance(handler.formatter, nmi_logging.JsonFormatter)


def test_metrics_registry_counters_gauges_and_histograms():
    REGISTRY.reset()
    REGISTRY.inc_counter("http_requests_total", labels={"path": "/x"}, value=1.0)
    REGISTRY.set_gauge("queue_depth", 5)
    REGISTRY.observe("latency", 0.01)
    REGISTRY.observe("latency", 2.0)
    out = REGISTRY.render()

    assert "# TYPE http_requests_total counter" in out
    assert 'http_requests_total{path="/x"}' in out
    assert "# TYPE queue_depth gauge" in out
    assert "queue_depth 5" in out
    assert "# TYPE latency histogram" in out
    assert 'latency_bucket{le="0.025"}' in out
    assert 'latency_bucket{le="+Inf"}' in out
    assert "latency_sum" in out
    assert "latency_count" in out
