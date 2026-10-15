"""Stress tests: historical windows, shock severity, and Monte Carlo simulation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .performance import wealth_and_drawdown

# Well-known episodes; only those inside the uploaded history are offered. Windows run from the market peak to
# the trough. The 2023-2026 windows were dated from daily S&P 500, Russell 2000 and MSCI EM ETF data.
KNOWN_WINDOWS = [
    ("Dot-Com bubble burst", "2000-03-01", "2002-10-31"),
    ("Global financial crisis", "2007-10-01", "2009-03-31"),
    ("European debt crisis", "2011-05-01", "2012-07-31"),
    ("Oil price crash", "2014-07-01", "2016-02-29"),
    ("Q4 2018 sell-off", "2018-10-01", "2018-12-31"),
    ("COVID-19 crash", "2020-02-01", "2020-04-30"),
    ("2022 inflation shock", "2022-01-01", "2022-10-31"),
    ("2023 US regional bank crisis (SVB)", "2023-02-02", "2023-03-23"),
    ("2023 bond sell-off (higher for longer)", "2023-07-31", "2023-10-27"),
    ("Aug 2024 yen carry-trade unwind", "2024-07-16", "2024-08-05"),
    ("2025 tariff shock", "2025-02-19", "2025-04-08"),
    ("Q1 2026 equity sell-off", "2026-01-27", "2026-03-30"),
    ("Mid-2026 emerging-market sell-off", "2026-06-22", "2026-07-29"),
]


def drawdown_episodes(dates: pd.DatetimeIndex, series: np.ndarray, threshold: float) -> list[dict]:
    """Peak-to-trough falls deeper than `threshold` in a return series, newest first.

    An episode that has not yet recovered to its previous peak is flagged as ongoing.
    """
    wealth, dd = wealth_and_drawdown(series)
    out, k, n = [], 1, len(wealth)
    while k < n:
        if dd[k] >= 0:
            k += 1
            continue
        peak = k - 1                                 # last running high before the fall
        j = k
        while j < n and dd[j] < 0:
            j += 1
        trough = peak + 1 + int(np.argmin(wealth[peak + 1:j]))
        depth = float(wealth[trough] / wealth[peak] - 1)
        if depth <= -threshold:
            out.append({"start": str(dates[peak].date()), "end": str(dates[trough - 1].date()),
                        "depth": depth, "ongoing": j >= n})
        k = j + 1
    return out[::-1]


def suggest_windows(dates: pd.DatetimeIndex, portfolio: np.ndarray, ppy: int, max_detected: int = 3) -> list[dict]:
    """Known crises covered by the data, the portfolio's own worst drawdown, and its recent large drawdowns.

    Detected drawdowns keep the list current beyond the named crises: any fall of at least half the
    portfolio's annual volatility (and at least 5%) that does not overlap a named window is added.
    """
    out = []
    min_obs = max(3, ppy // 52)                      # a three-week crisis is visible in daily or weekly data
    for name, start, end in KNOWN_WINDOWS:
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        inside = (dates >= s) & (dates <= e)
        covered = (min(e, dates[-1]) - max(s, dates[0])).days / max((e - s).days, 1)
        if inside.sum() < min_obs or covered < 0.5:                 # keep windows the data mostly covers
            continue
        if dates[0] > s + pd.Timedelta(days=7) or dates[-1] < e - pd.Timedelta(days=7):
            s0, e0 = max(s, dates[0]), min(e, dates[-1])
            out.append({"name": f"{name} (partial: data covers {s0:%b %Y} to {e0:%b %Y})", "start": str(s0.date()),
                        "end": str(e0.date()), "kind": "named"})
        else:
            out.append({"name": name, "start": start, "end": end, "kind": "named"})
    if len(portfolio) < 2:
        return out
    wealth, dd = wealth_and_drawdown(portfolio)
    trough = int(dd.argmin())
    if trough > 0 and dd[trough] < -0.02:
        peak = int(wealth[:trough + 1].argmax())
        ongoing = wealth[trough:].max() < wealth[peak]
        out.append({"name": "Current drawdown, the worst in this data (not yet recovered)" if ongoing else "Worst drawdown in this data",
                    "start": str(dates[max(peak, 0)].date()),
                    "end": str(dates[trough - 1].date()), "kind": "detected"})
    vol = float(np.std(portfolio, ddof=1) * np.sqrt(ppy))
    overlaps = lambda a, b: pd.Timestamp(a["start"]) <= pd.Timestamp(b["end"]) and pd.Timestamp(b["start"]) <= pd.Timestamp(a["end"])
    added = 0
    for ep in drawdown_episodes(dates, portfolio, max(0.05, 0.5 * vol)):
        if added >= max_detected or any(overlaps(ep, w) for w in out):
            continue
        label = "Current drawdown (not yet recovered)" if ep["ongoing"] else \
            f"Drawdown {pd.Timestamp(ep['start']):%b %Y} to {pd.Timestamp(ep['end']):%b %Y}"
        out.append({"name": label, "start": ep["start"], "end": ep["end"], "kind": "detected"})
        added += 1
    return out


def window_analysis(returns: np.ndarray, dates: pd.DatetimeIndex, weights, windows: list[dict], *,
                    ppy: int, rf: float = 0.0, extra_return: float = 0.0) -> list[dict]:
    """Asset and portfolio behaviour inside each window (period-rebalanced to the given weights).

    extra_return: annual return of the cash line net of borrowing and short costs (from the mandate).
    """
    w = np.asarray(weights, dtype=float)
    out = []
    for win in windows:
        mask = (dates >= pd.Timestamp(win["start"])) & (dates <= pd.Timestamp(win["end"]))
        r = returns[mask]
        if len(r) < 2:
            out.append({**win, "periods": int(len(r)), "error": "Fewer than two observations in this window."})
            continue
        port = r @ w + extra_return / ppy
        wealth, dd = wealth_and_drawdown(port)
        mean_ann, vol = float(port.mean() * ppy), float(port.std(ddof=1) * np.sqrt(ppy))
        out.append({
            **win, "periods": int(len(r)),
            "asset_return": (r.mean(axis=0) * ppy).tolist(),
            "asset_volatility": (r.std(axis=0, ddof=1) * np.sqrt(ppy)).tolist(),
            "asset_cumulative": (np.prod(1 + r, axis=0) - 1).tolist(),
            "mean_ann_return": mean_ann, "volatility": vol,
            "sharpe": (mean_ann - rf) / vol if vol > 0 else None,
            "cumulative": float(wealth[-1] - 1), "max_drawdown": float(dd.min()),
            "path": wealth.tolist(), "dates": [str(d.date()) for d in dates[mask]],
            "correlation": np.corrcoef(r, rowvar=False).tolist(),
        })
    return out


def shock_sensitivity(weights, base_mu, shock_mu, severities=None) -> dict:
    """Stressed return = base + severity x (shock - base). Severity 1 reproduces the shock scenario."""
    w, base, shock = (np.asarray(x, dtype=float) for x in (weights, base_mu, shock_mu))
    sev = np.asarray(severities if severities is not None else np.arange(0, 3.01, 0.25), dtype=float)
    asset = base[None, :] + sev[:, None] * (shock - base)[None, :]
    port = asset @ w
    drop = float(w @ base - w @ shock)
    return {"severity": sev.tolist(), "asset_return": asset.tolist(), "portfolio_return": port.tolist(),
            "base_return": float(w @ base), "shock_return": float(w @ shock),
            "break_even": float(w @ base) / drop if drop > 0 else None,
            "contribution_change": (w * (shock - base)).tolist()}


def monte_carlo(weights, mu, cov, *, ppy: int, years: float = 5.0, trials: int = 5000, method: str = "normal",
                seed: int = 42, history: np.ndarray | None = None, df: int = 5, rf: float = 0.0,
                block: int | None = None, extra_return: float = 0.0) -> dict:
    """Simulate a period-rebalanced portfolio from annual mu / cov (or by resampling history).

    method: "normal" (multivariate normal), "student_t" (fat tails, same covariance), "bootstrap"
    (blocks of actual history, keeping cross-asset and short-term time dependence).
    """
    w = np.asarray(weights, dtype=float)
    mu_p, cov_p = np.asarray(mu, dtype=float) / ppy, np.asarray(cov, dtype=float) / ppy
    steps, n = max(1, int(round(years * ppy))), len(w)
    rng = np.random.default_rng(seed)

    if method == "bootstrap":
        if history is None or len(history) < 12:
            raise ValueError("Bootstrap needs the uploaded return history.")
        block = block or max(1, ppy // 4)
        n_blocks = int(np.ceil(steps / block))
        starts = rng.integers(0, len(history) - block + 1, size=(trials, n_blocks))
        idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(trials, -1)[:, :steps]
        port = history[idx] @ w
    else:
        vals, vecs = np.linalg.eigh((cov_p + cov_p.T) / 2)
        root = vecs * np.sqrt(np.clip(vals, 0, None))           # works for near-singular matrices
        z = rng.standard_normal((trials, steps, n))
        if method == "student_t":
            if df <= 2:
                raise ValueError("Degrees of freedom must be above 2.")
            chi = rng.chisquare(df, size=(trials, steps, 1))
            z = z * np.sqrt((df - 2) / chi)                       # unit variance, heavier tails
        elif method != "normal":
            raise ValueError("method must be normal, student_t or bootstrap.")
        port = (mu_p + z @ root.T) @ w

    port = port + extra_return / ppy                          # cash, borrowing and short costs (mandate)
    wealth = np.cumprod(1 + np.maximum(port, -1.0), axis=1)
    terminal = wealth[:, -1]
    ann = np.where(terminal > 0, np.abs(terminal) ** (1 / years), 0.0) - 1
    running_peak = np.maximum.accumulate(np.concatenate([np.ones((trials, 1)), wealth], axis=1), axis=1)
    mdd = (np.concatenate([np.ones((trials, 1)), wealth], axis=1) / running_peak - 1).min(axis=1)
    vol = port.std(axis=1, ddof=1) * np.sqrt(ppy) if steps > 1 else np.zeros(trials)
    pct = lambda a, q: float(np.percentile(a, q))
    q05 = pct(ann, 5)
    fan_q = [5, 25, 50, 75, 95]
    fan = np.percentile(np.concatenate([np.ones((trials, 1)), wealth], axis=1), fan_q, axis=0)
    lo, hi = np.percentile(ann, [0.5, 99.5])
    counts, edges = np.histogram(np.clip(ann, lo, hi), bins=40)
    return {
        "method": method, "trials": trials, "periods": steps, "years": steps / ppy,
        "mean_ann_return": float(ann.mean()), "median_ann_return": pct(ann, 50),
        "p05_ann_return": q05, "p95_ann_return": pct(ann, 95),
        "cvar_95_ann_return": float(ann[ann <= q05].mean()),
        "mean_volatility": float(vol.mean()),
        "sharpe": float((ann.mean() - rf) / vol.mean()) if vol.mean() > 0 else None,
        "prob_loss": float((terminal < 1).mean()), "worst_ann_return": float(ann.min()),
        "median_terminal": pct(terminal, 50), "p05_terminal": pct(terminal, 5), "p95_terminal": pct(terminal, 95),
        "median_max_drawdown": pct(mdd, 50), "p05_max_drawdown": pct(mdd, 5),
        "fan": {"percentiles": fan_q, "wealth": fan.tolist()},
        "histogram": {"edges": edges.tolist(), "counts": counts.tolist()},
    }

