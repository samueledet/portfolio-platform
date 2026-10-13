"""Mean-variance optimisation under weight and group limits (convex problems solved with CVXPY)."""
from __future__ import annotations

import cvxpy as cp
import numpy as np

OBJECTIVES = ("min_variance", "max_return", "max_sharpe", "target_volatility", "target_return", "risk_parity")


class Infeasible(ValueError):
    """No portfolio satisfies the constraints (message says which one to relax)."""


def _bounds(n: int, constraints: dict | None) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    c = constraints or {}
    lower = np.asarray(c.get("lower") if c.get("lower") is not None else [0.0] * n, dtype=float)
    upper = np.asarray(c.get("upper") if c.get("upper") is not None else [1.0] * n, dtype=float)
    if lower.shape != (n,) or upper.shape != (n,):
        raise ValueError(f"Weight limits need one value per asset ({n}).")
    if (lower > upper + 1e-12).any():
        raise Infeasible("A minimum weight is above its maximum.")
    if lower.sum() > 1 + 1e-9:
        raise Infeasible(f"Minimum weights add up to {lower.sum():.0%}, more than 100%.")
    if upper.sum() < 1 - 1e-9:
        raise Infeasible(f"Maximum weights add up to {upper.sum():.0%}, less than 100%.")
    return lower, upper, list(c.get("groups") or [])


def _base_constraints(w, scale, lower, upper, groups):
    """Constraints on w (scale = 1) or on y = scale * w for the max-Sharpe transform."""
    cons = [cp.sum(w) == scale, w >= lower * scale, w <= upper * scale]
    for g in groups:
        idx = list(g["assets"])
        if g.get("min") is not None:
            cons.append(cp.sum(w[idx]) >= float(g["min"]) * scale)
        if g.get("max") is not None:
            cons.append(cp.sum(w[idx]) <= float(g["max"]) * scale)
    return cons


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
    raise Infeasible(f"No portfolio meets the constraints for {what}. Relax a weight, group or risk limit.")


def describe(w: np.ndarray, mu: np.ndarray, cov: np.ndarray, rf: float = 0.0) -> dict:
    w = np.where(np.abs(w) < 1e-7, 0.0, w)
    w = w / w.sum()
    ret, var = float(w @ mu), float(w @ cov @ w)
    vol = float(np.sqrt(max(var, 0.0)))
    marginal = cov @ w
    risk_share = (w * marginal / var).tolist() if var > 0 else [0.0] * len(w)
    return {"weights": w.tolist(), "exp_return": ret, "volatility": vol,
            "sharpe": (ret - rf) / vol if vol > 0 else None, "risk_contribution": risk_share}


def optimise(mu, cov, objective: str = "max_sharpe", *, rf: float = 0.0, target: float | None = None,
             constraints: dict | None = None) -> dict:
    """Return the optimal portfolio for one objective.

    target_volatility maximises return subject to volatility <= target (the convex form of Solver's
    "maximise return at a target risk"); target_return minimises risk subject to return >= target.
    """
    mu, cov = np.asarray(mu, dtype=float), np.asarray(cov, dtype=float)
    n = len(mu)
    cov = (cov + cov.T) / 2
    lower, upper, groups = _bounds(n, constraints)
    S = cp.psd_wrap(cov)
    w = cp.Variable(n)
    cons = _base_constraints(w, 1, lower, upper, groups)

    if objective == "min_variance":
        _solve(cp.Problem(cp.Minimize(cp.quad_form(w, S)), cons), "minimum variance")
    elif objective == "max_return":
        _solve(cp.Problem(cp.Maximize(mu @ w), cons), "maximum return")
    elif objective == "target_volatility":
        if target is None or target <= 0:
            raise ValueError("Give a positive target volatility.")
        floor = optimise(mu, cov, "min_variance", rf=rf, constraints=constraints)["volatility"]
        if target < floor - 1e-9:
            raise Infeasible(f"Target volatility {target:.2%} is below the minimum achievable {floor:.2%}.")
        _solve(cp.Problem(cp.Maximize(mu @ w), cons + [cp.quad_form(w, S) <= target ** 2]), "the volatility limit")
    elif objective == "target_return":
        if target is None:
            raise ValueError("Give a target return.")
        _solve(cp.Problem(cp.Minimize(cp.quad_form(w, S)), cons + [mu @ w >= target]),
               f"a target return of {target:.2%}")
    elif objective == "max_sharpe":
        excess = mu - rf
        if excess.max() <= 0:
            raise Infeasible("No asset has an expected return above the risk-free rate, so Sharpe cannot be maximised.")
        y, k = cp.Variable(n), cp.Variable(nonneg=True)
        prob = cp.Problem(cp.Minimize(cp.quad_form(y, S)),
                          _base_constraints(y, k, lower, upper, groups) + [excess @ y == 1])
        _solve(prob, "maximum Sharpe ratio")
        return {**describe(np.asarray(y.value) / float(k.value), mu, cov, rf), "objective": objective}
    elif objective == "risk_parity":
        y = cp.Variable(n, pos=True)
        _solve(cp.Problem(cp.Minimize(0.5 * cp.quad_form(y, S) - cp.sum(cp.log(y)) / n)), "risk parity")
        return {**describe(np.asarray(y.value), mu, cov, rf), "objective": objective,
                "note": "Risk parity equalises risk contributions; weight and group limits are not applied."}
    else:
        raise ValueError(f"Unknown objective '{objective}'. Choose one of {', '.join(OBJECTIVES)}.")
    return {**describe(np.asarray(w.value), mu, cov, rf), "objective": objective}


def efficient_frontier(mu, cov, *, rf: float = 0.0, constraints: dict | None = None, points: int = 40) -> dict:
    """Minimum-variance portfolios for evenly spaced returns between the min-variance and max-return ends."""
    mu, cov = np.asarray(mu, dtype=float), np.asarray(cov, dtype=float)
    lo = optimise(mu, cov, "min_variance", rf=rf, constraints=constraints)
    hi = optimise(mu, cov, "max_return", rf=rf, constraints=constraints)
    n = len(mu)
    lower, upper, groups = _bounds(n, constraints)
    w, r = cp.Variable(n), cp.Parameter()
    prob = cp.Problem(cp.Minimize(cp.quad_form(w, cp.psd_wrap((cov + cov.T) / 2))),
                      _base_constraints(w, 1, lower, upper, groups) + [mu @ w >= r])
    out = [lo]
    for target in np.linspace(lo["exp_return"], hi["exp_return"], points)[1:-1]:
        r.value = float(target)
        try:
            _solve(prob, "the frontier")
        except Infeasible:
            continue
        out.append(describe(np.asarray(w.value), mu, cov, rf))
    out.append(hi)
    return {"points": out, "min_variance": lo, "max_return": hi}
