"""Portfolio return series: backtests with rebalancing, and performance / risk metrics."""
from __future__ import annotations

import numpy as np

from .stats import excess_kurtosis, skewness

REBALANCE_EVERY = {"every_period": 1, "monthly": 1 / 12, "quarterly": 1 / 4, "annual": 1.0, "never": None}


def portfolio_returns(returns: np.ndarray, weights, *, ppy: int, rebalance: str = "every_period",
                      cost_bps: float = 0.0) -> tuple[np.ndarray, float]:
    """Period returns of a portfolio that drifts between rebalances back to target weights.

    Returns (series, average annual one-way turnover). cost_bps is charged on traded value at each rebalance.
    """
    target = np.asarray(weights, dtype=float)
    target = target / target.sum()
    if rebalance not in REBALANCE_EVERY:
        raise ValueError(f"rebalance must be one of {', '.join(REBALANCE_EVERY)}.")
    span = REBALANCE_EVERY[rebalance]
    if span is None:
        step = None                                   # buy and hold
    elif rebalance == "every_period":
        step = 1
    else:
        step = max(1, int(round(span * ppy)))        # periods between rebalances
    w, out, turnover = target.copy(), np.empty(len(returns)), 0.0
    for t, r in enumerate(returns):
        cost = 0.0
        if step is not None and t > 0 and t % step == 0:
            traded = np.abs(target - w).sum() / 2
            turnover += traded
            cost = traded * 2 * cost_bps / 1e4
            w = target.copy()
        gross = float(w @ r)
        out[t] = (1 + gross) * (1 - cost) - 1
        w = w * (1 + r) / (1 + gross)
    years = len(returns) / ppy
    return out, turnover / years if years > 0 else 0.0


def wealth_and_drawdown(r: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    wealth = np.concatenate([[1.0], np.cumprod(1 + r)])
    return wealth, wealth / np.maximum.accumulate(wealth) - 1


def metrics(r: np.ndarray, *, ppy: int, rf: float = 0.0, mar: float | None = None,
            benchmark: np.ndarray | None = None) -> dict:
    """Annualised performance and risk statistics for one return series.

    rf and mar are annual rates. Sortino uses the per-period MAR and annualised downside deviation.
    """
    r = np.asarray(r, dtype=float)
    n = len(r)
    wealth, dd = wealth_and_drawdown(r)
    ann_return = float(wealth[-1] ** (ppy / n) - 1)
    mean_ann = float((1 + r.mean()) ** ppy - 1)
    vol = float(r.std(ddof=1) * np.sqrt(ppy))
    mar = rf if mar is None else mar
    mar_p = (1 + mar) ** (1 / ppy) - 1
    downside = float(np.sqrt((np.minimum(r - mar_p, 0) ** 2).mean() * ppy))
    q05 = float(np.percentile(r, 5))
    tail = r[r <= q05]
    out = {
        "periods": n, "ann_return": ann_return, "mean_ann_return": mean_ann, "volatility": vol,
        "sharpe": (ann_return - rf) / vol if vol > 0 else None,
        "downside_deviation": downside, "sortino": (mean_ann - mar) / downside if downside > 0 else None,
        "cumulative": float(wealth[-1] - 1), "max_drawdown": float(dd.min()),
        "calmar": ann_return / abs(dd.min()) if dd.min() < 0 else None,
        "worst": float(r.min()), "best": float(r.max()), "positive_share": float((r > 0).mean()),
        "skew": skewness(r), "kurtosis": excess_kurtosis(r),
        "var_95": -q05, "cvar_95": float(-tail.mean()) if tail.size else -q05,
    }
    if benchmark is not None:
        b = np.asarray(benchmark, dtype=float)
        active = r - b
        te = float(active.std(ddof=1) * np.sqrt(ppy))
        b_ann = float(np.prod(1 + b) ** (ppy / n) - 1)
        var_b = b.var(ddof=1)
        beta = float(np.cov(r, b, ddof=1)[0, 1] / var_b) if var_b > 0 else None
        out.update({
            "active_return": ann_return - b_ann, "tracking_error": te,
            "information_ratio": (ann_return - b_ann) / te if te > 0 else None, "beta": beta,
            "alpha": (ann_return - rf) - beta * (b_ann - rf) if beta is not None else None,
            "up_capture": float(r[b > 0].mean() / b[b > 0].mean()) if (b > 0).any() else None,
            "down_capture": float(r[b < 0].mean() / b[b < 0].mean()) if (b < 0).any() else None,
        })
    return out
