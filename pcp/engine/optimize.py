"""Portfolio optimisation under weight, group and mandate limits (convex problems solved with CVXPY).

Mandates (see mandate.py) add net and gross exposure limits, short positions with borrow costs, cash and
leverage, turnover limits, and relative risk against a benchmark or a liability.
"""
from __future__ import annotations

import cvxpy as cp
import numpy as np

from .mandate import Mandate

OBJECTIVES = ("min_variance", "max_return", "max_sharpe", "target_volatility", "target_return", "risk_parity", "min_cvar")


class Infeasible(ValueError):
    """No portfolio satisfies the constraints (message says which one to relax)."""


def _bounds(n: int, constraints: dict | None, m: Mandate) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    c = constraints or {}
    lower = np.asarray(c.get("lower") if c.get("lower") is not None else [(-1.0 if m.shorts else 0.0)] * n, dtype=float)
    upper = np.asarray(c.get("upper") if c.get("upper") is not None else [max(1.0, m.net_max)] * n, dtype=float)
    if lower.shape != (n,) or upper.shape != (n,):
        raise ValueError(f"Weight limits need one value per asset ({n}).")
    if (lower < -1e-12).any() and not m.shorts:
        raise Infeasible("Negative minimum weights need a mandate that allows short positions.")
    if (lower > upper + 1e-12).any():
        raise Infeasible("A minimum weight is above its maximum.")
    if lower.sum() > m.net_max + 1e-9:
        raise Infeasible(f"Minimum weights add up to {lower.sum():.0%}, more than the {m.net_max:.0%} the mandate allows.")
    if upper.sum() < m.net_min - 1e-9:
        raise Infeasible(f"Maximum weights add up to {upper.sum():.0%}, less than the {m.net_min:.0%} the mandate requires.")
    return lower, upper, list(c.get("groups") or [])


def _constraints(w, scale, lower, upper, groups, m: Mandate, S=None):
    """Constraints on w (scale = 1) or on y = scale * w for the max-Sharpe transform."""
    cons = [w >= lower * scale, w <= upper * scale]
    if m.net_min == m.net_max:
        cons.append(cp.sum(w) == m.net_min * scale)
    else:
        cons += [cp.sum(w) >= m.net_min * scale, cp.sum(w) <= m.net_max * scale]
    if m.gross_max is not None:
        cons.append(cp.norm1(w) <= m.gross_max * scale)
    for g in groups:
        idx = list(g["assets"])
        if g.get("min") is not None:
            cons.append(cp.sum(w[idx]) >= float(g["min"]) * scale)
        if g.get("max") is not None:
            cons.append(cp.sum(w[idx]) <= float(g["max"]) * scale)
    if m.turnover_max is not None:
        cons.append(cp.norm1(w - m.current * scale) <= m.turnover_max * scale)
    if m.active_limit is not None and m.reference is not None:
        cons.append(cp.abs(w - m.reference * scale) <= m.active_limit * scale)
    if m.risk_budget is not None and S is not None:            # relative mandates are solved with scale = 1
        cons.append(cp.quad_form(w - m.reference, S) <= m.risk_budget ** 2)
    return cons


def _return_expr(w, mu, rf, m: Mandate):
    """Expected return including cash interest, borrowing spread and short borrow costs (concave)."""
    expr = mu @ w + rf * (1 - cp.sum(w))
    if m.borrow_spread:
        expr = expr - m.borrow_spread * cp.pos(cp.sum(w) - 1)
    if m.short_cost:
        expr = expr - m.short_cost * cp.sum(cp.neg(w))
    return expr


def _solve(problem: cp.Problem, what: str) -> None:
    for solver in (cp.CLARABEL, cp.SCS):
        try:
            problem.solve(solver=solver)
        except cp.SolverError:
            continue
        if problem.status in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
            return
        if problem.status in (cp.INFEASIBLE, cp.INFEASIBLE_INACCURATE):
            break
    raise Infeasible(f"No portfolio meets the constraints for {what}. Relax a weight, group, exposure or risk limit.")


def describe(w, mu, cov, rf: float = 0.0, mandate: Mandate | None = None, normalise: bool | None = None) -> dict:
    """Weights plus expected return, risk and exposures; relative mandates add tracking error or surplus risk."""
    m = mandate or Mandate()
    w = np.where(np.abs(np.asarray(w, dtype=float)) < 1e-7, 0.0, np.asarray(w, dtype=float))
    if normalise if normalise is not None else m.full_investment:
        w = w / w.sum()
    mu, cov = np.asarray(mu, dtype=float), np.asarray(cov, dtype=float)
    ret, var = m.expected_return(w, mu, rf), float(w @ cov @ w)
    vol = float(np.sqrt(max(var, 0.0)))
    out = {"weights": w.tolist(), "exp_return": ret, "volatility": vol, "sharpe": (ret - rf) / vol if vol > 1e-12 else None,
           "risk_contribution": (w * (cov @ w) / var).tolist() if var > 1e-16 else [0.0] * len(w),
           "net": float(w.sum()), "gross": float(np.abs(w).sum()), "cash": float(1 - w.sum()),
           "short": float(np.maximum(-w, 0).sum()), "risk": vol, "reward": ret}
    if m.reference is not None:
        d = w - m.reference
        te = float(np.sqrt(max(d @ cov @ d, 0.0)))
        active = ret - float(m.reference @ mu)
        out.update({"tracking_error": te, "active_return": active, "information_ratio": active / te if te > 1e-12 else None,
                    "risk": te, "reward": active})
    if m.current is not None:
        out["turnover"] = float(np.abs(w - m.current).sum())
    return out


def cvar(losses: np.ndarray, beta: float = 0.95) -> float:
    """Average of the worst (1 - beta) share of losses."""
    var = np.quantile(losses, beta)
    tail = losses[losses >= var]
    return float(tail.mean()) if tail.size else float(var)


def optimise(mu, cov, objective: str = "max_sharpe", *, rf: float = 0.0, target: float | None = None,
             constraints: dict | None = None, scenarios: np.ndarray | None = None, beta: float = 0.95,
             mandate: Mandate | None = None, ppy: int = 12) -> dict:
    """Return the optimal portfolio for one objective under the mandate.

    Under a relative mandate "risk" is tracking error (or surplus risk) and "return" is active (or surplus) return.
      target_volatility  maximise return subject to risk <= target (Solver's "maximise return at a target risk")
      target_return      minimise risk subject to return >= target
      max_sharpe         best return per unit of risk (information ratio under a relative mandate)
      min_cvar           minimise the average loss in the worst (1 - beta) of historical periods
      risk_parity        equal risk contributions (long-only mandates only)
    """
    m = mandate or Mandate()
    mu, cov = np.asarray(mu, dtype=float), np.asarray(cov, dtype=float)
    n = len(mu)
    cov = (cov + cov.T) / 2
    lower, upper, groups = _bounds(n, constraints, m)
    S = cp.psd_wrap(cov)
    w = cp.Variable(n)
    cons = _constraints(w, 1, lower, upper, groups, m, S)
    d = m.reference if m.reference is not None else np.zeros(n)
    risk = cp.quad_form(w - d, S)
    ret = _return_expr(w, mu, rf, m)
    reward = ret - float(d @ mu) if m.reference is not None else ret
    risk_word = {"benchmark": "tracking error", "surplus": "surplus risk"}.get(m.relative, "volatility")
    done = lambda **extra: {**describe(np.asarray(w.value), mu, cov, rf, m, normalise=False), "objective": objective, **extra}

    if objective == "min_variance":
        _solve(cp.Problem(cp.Minimize(risk), cons), f"minimum {risk_word}")
    elif objective == "max_return":
        _solve(cp.Problem(cp.Maximize(ret), cons), "maximum return")
    elif objective == "target_volatility":
        if target is None or target <= 0:
            raise ValueError(f"Give a positive target {risk_word}.")
        floor = optimise(mu, cov, "min_variance", rf=rf, constraints=constraints, mandate=m)["risk"]
        if target < floor - 1e-9:
            raise Infeasible(f"Target {risk_word} {target:.2%} is below the minimum achievable {floor:.2%}.")
        _solve(cp.Problem(cp.Maximize(ret), cons + [risk <= target ** 2]), f"the {risk_word} limit")
    elif objective == "target_return":
        if target is None:
            raise ValueError("Give a target return.")
        _solve(cp.Problem(cp.Minimize(risk), cons + [reward >= target]), f"a target return of {target:.2%}")
    elif objective == "min_cvar":
        if scenarios is None or len(scenarios) < 20:
            raise ValueError("CVaR optimisation needs at least 20 periods of return history.")
        if not 0.5 <= beta < 1:
            raise ValueError("The CVaR confidence level must be between 50% and 100%.")
        R = np.asarray(scenarios, dtype=float)
        rf_p = (1 + rf) ** (1 / ppy) - 1
        period = R @ (w - d) + rf_p * (1 - cp.sum(w))
        a, u = cp.Variable(), cp.Variable(len(R), nonneg=True)
        extra = [u >= -period - a] + ([reward >= target] if target is not None else [])
        _solve(cp.Problem(cp.Minimize(a + cp.sum(u) / ((1 - beta) * len(R))), cons + extra),
               "minimum CVaR" + (f" with a return of at least {target:.2%}" if target is not None else ""))
        wv = np.asarray(w.value)
        return done(beta=beta, period_cvar=cvar(-(R @ (wv - d) + rf_p * (1 - wv.sum())), beta))
    elif objective == "max_sharpe":
        if m.simple:
            excess = mu - rf
            if excess.max() <= 0:
                raise Infeasible("No asset has an expected return above the risk-free rate, so Sharpe cannot be maximised.")
            y, k = cp.Variable(n), cp.Variable(nonneg=True)
            _solve(cp.Problem(cp.Minimize(cp.quad_form(y, S)), _constraints(y, k, lower, upper, groups, m) + [excess @ y == 1]),
                   "maximum Sharpe ratio")
            return {**describe(np.asarray(y.value) / float(k.value), mu, cov, rf, m), "objective": objective}
        return _best_ratio(mu, cov, rf, constraints, m)
    elif objective == "risk_parity":
        if not (m.kind == "long_only" and m.reference is None):
            raise Infeasible("Risk parity is available for long-only, fully invested mandates.")
        y = cp.Variable(n, pos=True)
        _solve(cp.Problem(cp.Minimize(0.5 * cp.quad_form(y, S) - cp.sum(cp.log(y)) / n)), "risk parity")
        return {**describe(np.asarray(y.value), mu, cov, rf, m), "objective": objective,
                "note": "Risk parity equalises risk contributions; weight and group limits are not applied."}
    else:
        raise ValueError(f"Unknown objective '{objective}'. Choose one of {', '.join(OBJECTIVES)}.")
    return done()


def _best_ratio(mu, cov, rf, constraints, m: Mandate) -> dict:
    """Highest reward-to-risk ratio along the frontier: a scan, then a golden-section search between neighbours."""
    base = rf if m.reference is None else 0.0
    ratio = lambda p: (p["reward"] - base) / p["risk"] if p["risk"] > 1e-10 else -np.inf
    front = efficient_frontier(mu, cov, rf=rf, constraints=constraints, mandate=m, points=40)
    pts = front["points"]
    k = int(np.argmax([ratio(p) for p in pts]))
    best = pts[k]
    lo, hi = pts[max(k - 1, 0)]["reward"], pts[min(k + 1, len(pts) - 1)]["reward"]
    g = (np.sqrt(5) - 1) / 2
    for _ in range(18):
        if hi - lo < 1e-7:
            break
        a, b = hi - g * (hi - lo), lo + g * (hi - lo)
        try:
            pa = optimise(mu, cov, "target_return", rf=rf, target=a, constraints=constraints, mandate=m)
            pb = optimise(mu, cov, "target_return", rf=rf, target=b, constraints=constraints, mandate=m)
        except Infeasible:
            break
        if ratio(pa) >= ratio(pb):
            hi = b
            best = pa if ratio(pa) > ratio(best) else best
        else:
            lo = a
            best = pb if ratio(pb) > ratio(best) else best
    if ratio(best) <= 0:
        raise Infeasible("No portfolio earns more than the risk-free rate (or the benchmark), so the ratio cannot be maximised.")
    return {**best, "objective": "max_sharpe"}


def efficient_frontier(mu, cov, *, rf: float = 0.0, constraints: dict | None = None, points: int = 40,
                       mandate: Mandate | None = None) -> dict:
    """Lowest-risk portfolios for evenly spaced returns between the minimum-risk and maximum-return ends."""
    m = mandate or Mandate()
    mu, cov = np.asarray(mu, dtype=float), np.asarray(cov, dtype=float)
    lo = optimise(mu, cov, "min_variance", rf=rf, constraints=constraints, mandate=m)
    hi = optimise(mu, cov, "max_return", rf=rf, constraints=constraints, mandate=m)
    n = len(mu)
    lower, upper, groups = _bounds(n, constraints, m)
    d = m.reference if m.reference is not None else np.zeros(n)
    w, r = cp.Variable(n), cp.Parameter()
    ret = _return_expr(w, mu, rf, m)
    reward = ret - float(d @ mu) if m.reference is not None else ret
    S = cp.psd_wrap((cov + cov.T) / 2)
    prob = cp.Problem(cp.Minimize(cp.quad_form(w - d, S)), _constraints(w, 1, lower, upper, groups, m, S) + [reward >= r])
    out = [lo]
    for target in np.linspace(lo["reward"], hi["reward"], points)[1:-1]:
        r.value = float(target)
        try:
            _solve(prob, "the frontier")
        except Infeasible:
            continue
        out.append(describe(np.asarray(w.value), mu, cov, rf, m, normalise=False))
    out.append(hi)
    return {"points": out, "min_variance": lo, "max_return": hi}
