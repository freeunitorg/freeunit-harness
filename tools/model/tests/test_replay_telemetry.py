"""Tests for Telemetry Ingestion, Deterministic Replay, and Root Cause Analysis."""

import json
import pytest

from tools.model.replay.analyzer import RootCauseAnalyzer
from tools.model.replay.ingest import TelemetryIngestor, TraceEvent
from tools.model.replay.replayer import DeterministicReplayer


def test_otlp_json_ingestion():
    ingestor = TelemetryIngestor()

    otlp_payload = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "name": "http_request",
                                "startTimeUnixNano": "1000000000",
                                "endTimeUnixNano": "1000500000",
                                "attributes": [
                                    {"key": "http.request.method", "value": {"stringValue": "GET"}},
                                    {"key": "url.path", "value": {"stringValue": "/api/v1"}},
                                    {"key": "http.response.status_code", "value": {"intValue": 200}},
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }

    events = ingestor.ingest_otlp_json(otlp_payload)
    assert len(events) == 2  # start and end
    assert events[0].timestamp_ns == 0
    assert events[1].timestamp_ns == 500000
    assert events[0].stream_id is None
    assert events[0].attributes["http.request.method"] == "GET"
    assert events[0].attributes["http.response.status_code"] == 200


def test_usdt_line_ingestion():
    ingestor = TelemetryIngestor()

    lines = [
        "# USDT trace dump",
        "[5000] freeunit:queue__enqueue stream=1 cookie=0 tracking=10 engine=0",
        "[6000] freeunit:worker_crash worker=100",
    ]

    events = ingestor.ingest_usdt_lines(lines)
    assert len(events) == 2
    assert events[0].event_name == "usdt_queue__enqueue"
    assert events[0].stream_id == 1
    assert events[0].subsystem == "app_queue"
    assert events[1].event_name == "usdt_worker_crash"
    assert events[1].worker_id == 100


def test_deterministic_replay_and_anomaly_detection():
    replayer = DeterministicReplayer(num_engines=2, num_workers=2)

    events = [
        TraceEvent(
            timestamp_ns=100,
            subsystem="router",
            event_name="otel_request_start",
            stream_id=1,
            attributes={"http.request.method": "GET", "url.path": "/"},
        ),
        TraceEvent(
            timestamp_ns=500,
            subsystem="worker",
            event_name="usdt_worker_crash",
            worker_id=100,
        ),
    ]

    replayer.load_events(events)
    summary = replayer.replay_all()

    assert summary.total_events == 2
    assert summary.replayed_events == 2
    assert summary.final_clock_ns > 0
    assert summary.worker_states[100] == "DEAD"


def test_root_cause_analyzer_and_litmus_generator():
    events = [
        TraceEvent(timestamp_ns=1000, subsystem="router", event_name="start", stream_id=1),
        TraceEvent(timestamp_ns=5000, subsystem="worker", event_name="crash", stream_id=1),
    ]
    replayer = DeterministicReplayer(num_engines=1, num_workers=1)
    replayer.load_events(events)
    summary = replayer.replay_all()

    analyzer = RootCauseAnalyzer(summary, events)
    c_code = analyzer.generate_c_litmus_test(
        test_name="nxt_shm_race_test", anomaly_type="sigkill_double_free"
    )

    assert "nxt_shm_race_test" in c_code
    assert "#include <nxt_app_queue.h>" in c_code
    assert "NXT_TEST_CHECK" in c_code
    assert "sigkill_double_free" in c_code


def test_otlp_request_method_replays_as_post():
    """A span with the names the server exports must replay as POST, not GET."""
    ingestor = TelemetryIngestor()
    payload = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "name": "http_request",
                                "startTimeUnixNano": "1000000000",
                                "endTimeUnixNano": "1000500000",
                                "attributes": [
                                    {"key": "http.request.method", "value": {"stringValue": "POST"}},
                                    {"key": "url.path", "value": {"stringValue": "/submit"}},
                                    {"key": "http.response.status_code", "value": {"intValue": 201}},
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    events = ingestor.ingest_otlp_json(payload)
    assert events[0].stream_id is None
    assert events[0].attributes["http.request.method"] == "POST"
    assert events[0].attributes["http.response.status_code"] == 201

    methods = []
    replayer = DeterministicReplayer(num_engines=2, num_workers=2)
    orig = replayer.router.on_listener_request

    def wrapped(*args, **kwargs):
        req = kwargs.get("req")
        if req is None and len(args) >= 3:
            req = args[2]
        methods.append(req.method)
        return orig(*args, **kwargs)

    replayer.router.on_listener_request = wrapped
    replayer.load_events(events)
    replayer.replay_all()
    assert methods == ["POST"]
