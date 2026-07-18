"""Active-return and risk statistics vs adjusted SPY (Task 9).

Timestamps are inner-aligned before comparison; drawdown is always the
positive magnitude; the bootstrap uses contiguous 5-day blocks with a fixed
seed; the Deflated Sharpe probability (Bailey & Lopez de Prado) counts the
COMPLETE registered trial count including failures.
"""
import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
import pandas as pd

BOOTSTRAP_BLOCK = 5
BOOTSTRAP_DRAWS = 2000


@dataclass(frozen=True)
class ActiveMetrics:
    portfolio_return: float
    benchmark_return: float
    active_return: float
    beta: float
    tracking_error: float
    information_ratio: float
    sharpe: float
    sortino: float
    max_drawdown_magnitude: float
    calmar: float
    deflated_sharpe_probability: float
    bootstrap_active_low: float
    bootstrap_active_high: float
    observations: int


def align_return_series(portfolio_values, spy_adjusted_values):
    """Timestamp-normalized inner alignment of two value series."""
    port = pd.Series(portfolio_values).astype(float)
    spy = pd.Series(spy_adjusted_values).astype(float)
    port.index = pd.to_datetime(port.index, utc=True).normalize()
    spy.index = pd.to_datetime(spy.index, utc=True).normalize()
    aligned = pd.concat(
        [port.rename("portfolio"), spy.rename("benchmark")],
        axis=1, join="inner").dropna()
    if len(aligned) < 2:
        raise ValueError(
            f"need at least 2 matching observations, got {len(aligned)} — "
            "an empty alignment is an error, not zero active return")
    return aligned


def _drawdown_magnitude(values):
    peaks = values.cummax()
    return float((1.0 - values / peaks).max())


def compute_active_metrics(aligned, annualization=252, bootstrap_seed=179):
    values = aligned[["portfolio", "benchmark"]].astype(float)
    port_ret = values["portfolio"].pct_change().dropna()
    bench_ret = values["benchmark"].pct_change().dropna()
    active = (port_ret - bench_ret).to_numpy()
    n = len(port_ret)

    portfolio_return = float(values["portfolio"].iloc[-1] / values["portfolio"].iloc[0] - 1)
    benchmark_return = float(values["benchmark"].iloc[-1] / values["benchmark"].iloc[0] - 1)
    active_return = portfolio_return - benchmark_return

    bench_var = float(np.var(bench_ret, ddof=1)) if n > 1 else 0.0
    beta = (float(np.cov(port_ret, bench_ret, ddof=1)[0, 1]) / bench_var
            if bench_var > 0 else 0.0)
    tracking_error = float(np.std(active, ddof=1)) * math.sqrt(annualization) \
        if n > 1 else 0.0
    mean_active_ann = float(np.mean(active)) * annualization
    information_ratio = mean_active_ann / tracking_error if tracking_error > 0 else 0.0

    vol = float(np.std(port_ret, ddof=1)) * math.sqrt(annualization) if n > 1 else 0.0
    mean_ann = float(np.mean(port_ret)) * annualization
    sharpe = mean_ann / vol if vol > 0 else 0.0
    downside = port_ret[port_ret < 0]
    downside_vol = (float(np.std(downside, ddof=1)) * math.sqrt(annualization)
                    if len(downside) > 1 else 0.0)
    sortino = mean_ann / downside_vol if downside_vol > 0 else 0.0

    max_dd = _drawdown_magnitude(values["portfolio"])
    calmar = mean_ann / max_dd if max_dd > 0 else 0.0

    daily_sharpe = sharpe / math.sqrt(annualization) if annualization else 0.0
    skew = float(pd.Series(port_ret).skew()) if n > 2 else 0.0
    kurt = float(pd.Series(port_ret).kurt()) + 3.0 if n > 3 else 3.0
    dsp = deflated_sharpe_probability(
        daily_sharpe, sample_count=max(n, 2), skew=skew, kurtosis=kurt, trials=1)

    low, high = _block_bootstrap_interval(active, bootstrap_seed)

    return ActiveMetrics(
        portfolio_return=portfolio_return,
        benchmark_return=benchmark_return,
        active_return=active_return,
        beta=beta,
        tracking_error=tracking_error,
        information_ratio=information_ratio,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown_magnitude=max_dd,
        calmar=calmar,
        deflated_sharpe_probability=dsp,
        bootstrap_active_low=low,
        bootstrap_active_high=high,
        observations=n,
    )


def _block_bootstrap_interval(active, seed, block=BOOTSTRAP_BLOCK,
                              draws=BOOTSTRAP_DRAWS, level=0.90):
    if len(active) == 0:
        return 0.0, 0.0
    rng = np.random.default_rng(seed)
    n = len(active)
    n_blocks = max(1, math.ceil(n / block))
    starts_max = max(1, n - block + 1)
    means = np.empty(draws)
    for i in range(draws):
        starts = rng.integers(0, starts_max, size=n_blocks)
        sample = np.concatenate([active[s:s + block] for s in starts])[:n]
        means[i] = sample.mean()
    alpha = (1.0 - level) / 2.0
    return (float(np.quantile(means, alpha)), float(np.quantile(means, 1 - alpha)))


def deflated_sharpe_probability(sharpe, sample_count, skew, kurtosis, trials):
    """Bailey/Lopez de Prado Deflated Sharpe: probability the observed
    (per-period) Sharpe exceeds the expected maximum from ``trials``
    independent zero-skill trials. ``trials`` is the COMPLETE registered
    experiment count, including failures."""
    trials = int(trials)
    if trials < 1:
        raise ValueError("trials must count every registered experiment (>=1)")
    n = int(sample_count)
    if n < 2:
        raise ValueError("sample_count must be at least 2")
    nd = NormalDist()
    if trials == 1:
        sr_star = 0.0
    else:
        e = math.e
        z1 = nd.inv_cdf(1.0 - 1.0 / trials)
        z2 = nd.inv_cdf(1.0 - 1.0 / (trials * e))
        # Expected max of `trials` iid N(0,1) Sharpe estimates, scaled by the
        # estimator's standard error.
        sr_star = math.sqrt(1.0 / (n - 1)) * ((1 - 0.5772156649) * z1 +
                                              0.5772156649 * z2)
    denom = math.sqrt(
        max(1e-12,
            (1.0 - skew * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe ** 2)
            / (n - 1)))
    return float(nd.cdf((sharpe - sr_star) / denom))
