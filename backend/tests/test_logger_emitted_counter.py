"""The context log buffer is trimmed FIFO, so its length is not a watermark.

intellistock_logger.log() trims each attached context buffer to max_lines
(default 500, set at broker.py:11942). An incremental writer that slices the
buffer by "how many lines have I already written" therefore stops producing
new lines forever once the count reaches the cap. The monotonic
context_log_lines_emitted() counter is what BacktestSteps' log watermark keys
off instead.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from intellistock_logger import intellistock_logger as logger  # noqa: E402


def _drain(ctx="backtest"):
    logger.clear_backtest_log_buffer()


def test_buffer_length_saturates_but_the_counter_keeps_climbing():
    buf = []
    logger.set_backtest_log_buffer(buf, max_lines=5)
    try:
        for i in range(12):
            logger.log("line %d" % i, "white")
        assert len(buf) == 5, "the buffer must be trimmed FIFO"
        assert logger.context_log_lines_emitted("backtest") == 12
    finally:
        _drain()


def test_the_surviving_lines_are_the_most_recent_ones():
    buf = []
    logger.set_backtest_log_buffer(buf, max_lines=3)
    try:
        for i in range(6):
            logger.log("L%d" % i, "white")
        assert [line.split("] ", 1)[-1] for line in buf] == ["L3", "L4", "L5"]
    finally:
        _drain()


def test_a_writer_can_slice_exactly_the_new_lines_from_the_counter():
    """The heartbeat's slice: n_new = min(emitted - written, len(buffer))."""
    buf = []
    logger.set_backtest_log_buffer(buf, max_lines=4)
    try:
        written = 0
        seen = []
        for batch in ([0, 1], [2, 3, 4], [5]):
            for i in batch:
                logger.log("m%d" % i, "white")
            emitted = logger.context_log_lines_emitted("backtest")
            n_new = min(max(emitted - written, 0), len(buf))
            seen.extend(buf[len(buf) - n_new:])
            written = emitted
        assert [line.split("] ", 1)[-1] for line in seen] == \
            ["m0", "m1", "m2", "m3", "m4", "m5"]
    finally:
        _drain()


def test_setting_a_new_buffer_resets_the_counter():
    logger.set_backtest_log_buffer([], max_lines=5)
    try:
        logger.log("x", "white")
        assert logger.context_log_lines_emitted("backtest") == 1
        logger.set_backtest_log_buffer([], max_lines=5)
        assert logger.context_log_lines_emitted("backtest") == 0
    finally:
        _drain()


def test_clearing_the_buffer_resets_the_counter():
    logger.set_backtest_log_buffer([], max_lines=5)
    logger.log("x", "white")
    logger.clear_backtest_log_buffer()
    assert logger.context_log_lines_emitted("backtest") == 0


def test_an_unknown_context_reports_zero():
    assert logger.context_log_lines_emitted("no_such_context") == 0


def test_contexts_count_independently():
    a, b = [], []
    logger.set_context_log_buffer("ctx_a", a, max_lines=10)
    logger.set_context_log_buffer("ctx_b", b, max_lines=10)
    try:
        logger.log("shared", "white")
        assert logger.context_log_lines_emitted("ctx_a") == 1
        assert logger.context_log_lines_emitted("ctx_b") == 1
        logger.clear_context_log_buffer("ctx_a")
        logger.log("only b", "white")
        assert logger.context_log_lines_emitted("ctx_a") == 0
        assert logger.context_log_lines_emitted("ctx_b") == 2
    finally:
        logger.clear_context_log_buffer("ctx_a")
        logger.clear_context_log_buffer("ctx_b")
