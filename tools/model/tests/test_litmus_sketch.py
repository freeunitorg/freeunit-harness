"""The replay C output is a sketch, not a FreeUnit test entry point."""

from tools.model.replay.analyzer import RootCauseAnalyzer
from tools.model.replay.replayer import ReplaySummary


def test_litmus_sketch_uses_three_arg_macro():
    analyzer = RootCauseAnalyzer(ReplaySummary(0, 0, 0, 0), [])
    code = analyzer.generate_c_litmus_test("nxt_repro_race_test", "queue_race")
    assert "NXT_TEST_SINGLE(" not in code
    assert "Sketch only" in code
    assert "NXT_TEST_CHECK(task->log, rc == NXT_OK, " in code
    assert "NXT_TEST_CHECK(rc == NXT_OK," not in code
