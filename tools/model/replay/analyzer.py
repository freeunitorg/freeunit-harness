"""Root-Cause Analyzer and Standalone C Litmus Test Generator.

Isolates critical race windows from replay traces and writes a sketch of a
C reproduction. The sketch is not a compilable FreeUnit test.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from tools.model.replay.ingest import TraceEvent
from tools.model.replay.replayer import ReplaySummary


class RootCauseAnalyzer:
    """Analyzes concurrency anomalies and generates C reproduction harnesses."""

    def __init__(self, summary: ReplaySummary, events: List[TraceEvent]) -> None:
        self.summary = summary
        self.events = events

    def isolate_critical_race_window(
        self,
        anomaly_index: int = 0,
        window_ns: int = 20_000,
    ) -> List[TraceEvent]:
        """Extracts trace events occurring within +/- window_ns of the anomaly."""
        if not self.summary.anomalies:
            return []

        if anomaly_index >= len(self.summary.anomalies):
            anomaly_index = 0

        target = self.summary.anomalies[anomaly_index]
        target_t = target["time_ns"]

        min_t = max(0, target_t - window_ns)
        max_t = target_t + window_ns

        return [e for e in self.events if min_t <= e.timestamp_ns <= max_t]

    def generate_c_litmus_test(
        self,
        test_name: str = "nxt_concurrency_repro_test",
        anomaly_type: str = "dual_channel_race",
    ) -> str:
        """Return a sketch of a C reproduction. It is not a compilable FreeUnit test."""
        code = f"""/*
 * Sketch only. This is not a compilable FreeUnit test.
 * Suggested test name: {test_name}
 * Target anomaly: {anomaly_type}
 *
 * If this is turned into a real test, add a row to the table in
 * src/test/nxt_tests.c. Use NXT_TEST_CHECK(log, cond, ...) with the log
 * as the first argument. Do not use NXT_TEST_SINGLE; that macro is not
 * in FreeUnit.
 */

#include <nxt_main.h>
#include <nxt_app_queue.h>
#include <nxt_tests.h>

static nxt_int_t
{test_name}_run(nxt_task_t *task)
{{
    nxt_app_queue_t volatile  q;
    int                       notify = 0;
    uint32_t                  cookie = 0;
    nxt_int_t                 rc;
    uint8_t                   payload[16] = "repro_stimulus";

    nxt_app_queue_init(&q);

    rc = nxt_app_queue_send_bound(&q, payload, sizeof(payload), 1, &notify, &cookie, 4);
    NXT_TEST_CHECK(task->log, rc == NXT_OK, "queue send bound failed");
    NXT_TEST_CHECK(task->log, notify != 0, "doorbell failed to ring under burst");

    uint8_t recv_buf[NXT_APP_QUEUE_MSG_SIZE];
    ssize_t n = nxt_app_queue_recv(&q, recv_buf, &cookie);
    NXT_TEST_CHECK(task->log, n == sizeof(payload), "queue recv length mismatch");
    NXT_TEST_CHECK(task->log, nxt_memcmp(payload, recv_buf, sizeof(payload)) == 0,
                   "corrupted payload");

    return NXT_OK;
}}
"""
        return code
