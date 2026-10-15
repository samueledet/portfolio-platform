"""Calendar rebalancing with tolerance bands: drift, breaches and the trades that fix them."""
from __future__ import annotations

import numpy as np

POLICIES = {
    "breached_to_target": "Trade only the holdings outside their band, back to target",
    "all_to_target": "If any holding is outside its band, bring every holding back to target",
    "to_band_edge": "Trade breached holdings only to the edge of their band (least trading)",
}


def bands(target, abs_band: float = 0.02, rel_band: float = 0.25) -> np.ndarray:
    """Tolerance per holding: the larger of an absolute band and a share of the target weight.

    With the defaults a 40% holding may drift ±10 points and a 5% holding ±2 points.
    """
    t = np.asarray(target, dtype=float)
    return np.maximum(abs_band, rel_band * t)


def _fill(post: np.ndarray, fixed: np.ndarray, current: np.ndarray) -> np.ndarray:
    """Spread whatever is left over (to make weights add up to 100%) across the holdings not fixed, pro rata."""
    free = ~fixed
    room = 1 - post[fixed].sum()
    base = current[free].sum()
    if free.any():
        post[free] = current[free] * room / base if base > 1e-12 else room / free.sum()
    return post


def plan(current, target, band, policy: str = "breached_to_target", balance: float | None = None,
         cost_bps: float = 0.0) -> dict:
    """Drift report and post-trade weights for one rebalancing decision (long-only, fully invested)."""
    if policy not in POLICIES:
        raise ValueError(f"Rebalancing policy must be one of: {', '.join(POLICIES)}.")
    c, t, b = (np.asarray(x, dtype=float) for x in (current, target, band))
    if not (c.shape == t.shape == b.shape) or c.ndim != 1:
        raise ValueError("Current weights, target weights and bands need one value per asset.")
    if (c < -1e-9).any() or (t < -1e-9).any():
        raise ValueError("Rebalancing works with long-only holdings; weights cannot be negative.")
    if c.sum() <= 0 or t.sum() <= 0:
        raise ValueError("Current and target weights must add up to more than zero.")
    c, t = c / c.sum(), t / t.sum()
    drift = c - t
    breached = np.abs(drift) > b + 1e-12
    if not breached.any():
        post = c.copy()
    elif policy == "all_to_target":
        post = t.copy()
    else:
        post, fixed = c.copy(), np.zeros_like(breached)
        for _ in range(len(c) + 1):                       # fixing one holding can push another out of its band
            out = (np.abs(post - t) > b + 1e-12) & ~fixed
            if not out.any():
                break
            edge = t + np.sign(post - t) * b
            post[out] = t[out] if policy == "breached_to_target" else edge[out]
            fixed |= out
            post = _fill(post, fixed, c)
        if (np.abs(post - t) > b + 1e-9).any():           # could not settle inside the bands: go to target
            post = t.copy()
    trades = post - c
    turnover = float(np.abs(trades).sum() / 2)
    out = {
        "current": c.tolist(), "target": t.tolist(), "band": b.tolist(), "drift": drift.tolist(),
        "breached": breached.tolist(), "n_breached": int(breached.sum()), "post": post.tolist(), "trades": trades.tolist(),
        "turnover": turnover, "cost": turnover * 2 * cost_bps / 1e4, "policy": policy, "policy_label": POLICIES[policy],
        "action": "No trades needed: every holding is inside its band." if not breached.any() else
                  f"{int(breached.sum())} holding(s) outside their band; {turnover:.1%} of the portfolio changes hands.",
    }
    if balance is not None:
        out.update({"balance": float(balance), "trade_amounts": (trades * balance).tolist(),
                    "current_amounts": (c * balance).tolist(), "post_amounts": (post * balance).tolist()})
    return out


def drifted(weights, growth) -> np.ndarray:
    """Weights after each holding grew by `growth` (value now / value then) with no trading."""
    w = np.asarray(weights, dtype=float) * np.asarray(growth, dtype=float)
    return w / w.sum()
