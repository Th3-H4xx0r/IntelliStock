"""downsample_history: the RUNNING BacktestResults write must show the true
start + full shape of the portfolio curve, not a last-3000-points tail slice
(which made long/high-cadence running backtests start mid-run with a bogus
portfolio_start_value). Keep first + last, evenly sample the middle, cap size."""

from broker_snapshot_helpers import downsample_history


def test_downsample_keeps_first_last_and_caps():
    hist = [{"timestamp": i, "value": float(i)} for i in range(9000)]
    out = downsample_history(hist, 3000)
    assert out[0] == hist[0]            # true start preserved
    assert out[-1] == hist[-1]          # latest point preserved
    assert len(out) <= 3000
    ts = [h["timestamp"] for h in out]
    assert ts == sorted(ts)             # order preserved, no dupes out of order


def test_downsample_small_is_identity():
    hist = [{"timestamp": i, "value": float(i)} for i in range(50)]
    assert downsample_history(hist, 3000) == hist


def test_downsample_empty_and_none():
    assert downsample_history([], 3000) == []
    assert downsample_history(None, 3000) == []
