"""Spending-policy simulation for endowments, foundations and sovereign funds.

Projects a fund that earns the portfolio's return, pays out under a spending rule and receives
contributions, and reports the chance of preserving its inflation-adjusted value.
"""
from __future__ import annotations

import numpy as np

RULES = ("percent_of_value", "smoothed", "hybrid", "fixed_real")


def simulate(weights, mu, cov, *, years: int = 30, trials: int = 5000, seed: int = 42,
             rule: str = "smoothed", rate: float = 0.04, smoothing_years: int = 3, hybrid_weight: float = 0.7,
             inflation: float = 0.025, inflation_vol: float = 0.01, contribution: float = 0.0,
             fee: float = 0.0, initial: float = 100.0) -> dict:
    """Annual simulation of fund value and spending.

    Rules (S = spending for the year, V = fund value at the previous year end):
      percent_of_value  S = rate x V
      smoothed          S = rate x average V over the last `smoothing_years` year ends
      hybrid            S = w x last year's S grown with inflation + (1 - w) x rate x V   (Yale / Stanford rule)
      fixed_real        S = rate x initial value, grown with inflation every year
    contribution is new money each year as a share of the initial value, grown with inflation.
    fee is an annual cost as a share of fund value. Returns are lognormal with the portfolio's
    expected return and volatility; inflation is normal and independent of returns.
    """
    if rule not in RULES:
        raise ValueError(f"Spending rule must be one of {', '.join(RULES)}.")
    if not 0 <= rate < 1:
        raise ValueError("The spending rate must be between 0% and 100%.")
    years = int(years)
    if years < 1 or years > 100:
        raise ValueError("The horizon must be between 1 and 100 years.")
    w = np.asarray(weights, dtype=float)
    w = w / w.sum()
    m, s = float(w @ np.asarray(mu, dtype=float)), float(np.sqrt(max(w @ np.asarray(cov, dtype=float) @ w, 0.0)))
    rng = np.random.default_rng(seed)
    # lognormal annual gross return with arithmetic mean (1 + m) and standard deviation s
    sig2 = np.log(1 + (s / (1 + m)) ** 2)
    gross = np.exp(np.log(1 + m) - sig2 / 2 + np.sqrt(sig2) * rng.standard_normal((trials, years)))
    infl = np.maximum(inflation + inflation_vol * rng.standard_normal((trials, years)), -0.5)
    price = np.cumprod(1 + infl, axis=1)                       # cumulative inflation at each year end

    value = np.empty((trials, years + 1))
    value[:, 0] = initial
    spend = np.empty((trials, years))
    for t in range(years):
        prev = value[:, t]
        if rule == "percent_of_value":
            s_t = rate * prev
        elif rule == "smoothed":
            s_t = rate * value[:, max(0, t - smoothing_years + 1):t + 1].mean(axis=1)
        elif rule == "hybrid":
            s_t = rate * prev if t == 0 else (hybrid_weight * spend[:, t - 1] * (1 + infl[:, t])
                                               + (1 - hybrid_weight) * rate * prev)
        else:
            s_t = rate * initial * price[:, t]
        grown = prev * gross[:, t] * (1 - fee)
        s_t = np.minimum(s_t, np.maximum(grown, 0.0))           # cannot spend more than the fund holds
        spend[:, t] = s_t
        value[:, t + 1] = np.maximum(grown - s_t + contribution * initial * price[:, t], 0.0)

    deflator = np.concatenate([np.ones((trials, 1)), price], axis=1)
    real_value, real_spend = value / deflator, spend / price
    q = [5, 25, 50, 75, 95]
    change = real_spend[:, 1:] / np.where(real_spend[:, :-1] > 0, real_spend[:, :-1], np.nan) - 1 if years > 1 else np.zeros((trials, 1))
    terminal = real_value[:, -1]
    return {
        "rule": rule, "rate": rate, "years": years, "trials": trials,
        "portfolio_return": m, "portfolio_volatility": s, "real_return": (1 + m) / (1 + inflation) - 1,
        "prob_preserve": float((terminal >= initial).mean()),
        "prob_below_70": float((real_value.min(axis=1) < 0.7 * initial).mean()),
        "prob_depleted": float((value[:, -1] <= 1e-9).mean()),
        "prob_spending_cut_10": float((np.nanmin(change, axis=1) < -0.10).mean()) if years > 1 else 0.0,
        "median_terminal_real": float(np.median(terminal)),
        "p05_terminal_real": float(np.percentile(terminal, 5)), "p95_terminal_real": float(np.percentile(terminal, 95)),
        "median_spending_real": float(np.median(real_spend.mean(axis=1))),
        "first_year_spending": float(np.median(spend[:, 0])),
        "total_spending_real_median": float(np.median(real_spend.sum(axis=1))),
        "spending_volatility": float(np.nanmedian(np.nanstd(change, axis=1, ddof=1))) if years > 2 else 0.0,
        "value_fan": {"percentiles": q, "values": np.percentile(real_value, q, axis=0).tolist()},
        "spending_fan": {"percentiles": q, "values": np.percentile(real_spend, q, axis=0).tolist()},
        "initial": initial,
    }


def compare_rates(weights, mu, cov, rates, **kwargs) -> list[dict]:
    """Preservation probability and spending level for several spending rates under the same rule."""
    out = []
    for r in rates:
        res = simulate(weights, mu, cov, rate=float(r), **kwargs)
        out.append({k: res[k] for k in ("rate", "prob_preserve", "prob_below_70", "median_terminal_real",
                                        "median_spending_real", "prob_spending_cut_10", "spending_volatility")})
    return out
