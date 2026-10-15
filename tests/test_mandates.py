"""Mandates beyond long-only: each must respect its limits and behave as finance says it should."""
import json
import pathlib

import numpy as np
import pandas as pd
import pytest

from pcp.engine import mandate as md
from pcp.engine import optimize, performance, risk, spending, stats

u4 = json.loads((pathlib.Path(__file__).parent / "fixtures/unit4.json").read_text(encoding="utf-8"))
R = np.array(u4["indexReturns"])                       # monthly, 6 asset classes
A = stats.resolve_assumptions(R, 12, None)
MU, COV, N = A["mu"], A["cov"], 6
RF = 0.02


def opt(kind_or_spec, objective="max_sharpe", **kw):
    spec = {"kind": kind_or_spec} if isinstance(kind_or_spec, str) else kind_or_spec
    return optimize.optimise(MU, COV, objective, rf=RF, mandate=md.parse(spec, N), scenarios=R, **kw)


def test_long_only_is_unchanged_by_the_mandate_layer():
    a, b = optimize.optimise(MU, COV, "max_sharpe", rf=RF), opt("long_only")
    np.testing.assert_allclose(a["weights"], b["weights"], atol=1e-8)


def test_130_30_respects_net_and_gross_and_beats_long_only():
    p = opt({"kind": "long_short", "net": 1.0, "gross": 1.6}, "target_volatility", target=0.08)
    w = np.array(p["weights"])
    assert w.sum() == pytest.approx(1, abs=1e-6) and np.abs(w).sum() <= 1.6 + 1e-5 and w.min() < -0.01
    lo = opt("long_only", "target_volatility", target=0.08)
    assert p["exp_return"] >= lo["exp_return"] - 1e-7            # a wider feasible set cannot do worse


def test_short_borrow_cost_reduces_shorting():
    free = opt({"kind": "long_short", "gross": 1.6}, "target_volatility", target=0.08)
    dear = opt({"kind": "long_short", "gross": 1.6, "short_cost": 0.05}, "target_volatility", target=0.08)
    assert dear["short"] <= free["short"] + 1e-6 and dear["exp_return"] <= free["exp_return"] + 1e-9


def test_market_neutral_holds_cash_and_earns_the_risk_free_rate_plus_spread():
    p = opt({"kind": "market_neutral", "gross": 2.0}, "target_volatility", target=0.05)
    w = np.array(p["weights"])
    assert w.sum() == pytest.approx(0, abs=1e-6) and np.abs(w).sum() <= 2 + 1e-5 and p["cash"] == pytest.approx(1, abs=1e-6)
    assert p["exp_return"] == pytest.approx(float(w @ MU) + RF, abs=1e-8)
    zero = optimize.describe(np.zeros(N), MU, COV, RF, md.parse({"kind": "market_neutral"}, N))
    assert zero["exp_return"] == pytest.approx(RF) and zero["volatility"] == 0      # all cash earns rf


def test_leverage_raises_return_at_high_risk_and_pays_the_borrowing_spread():
    lev = opt({"kind": "leveraged", "leverage": 1.5, "borrow_spread": 0.01}, "target_volatility", target=0.14)
    w = np.array(lev["weights"])
    assert 1 - 1e-6 <= w.sum() <= 1.5 + 1e-6 and w.min() >= -1e-7
    assert lev["exp_return"] == pytest.approx(float(w @ MU) - (w.sum() - 1) * (RF + 0.01), abs=1e-7)
    assert lev["exp_return"] >= opt("long_only", "target_volatility", target=0.14)["exp_return"] - 1e-7


def test_cash_mandate_can_sit_in_cash():
    p = opt({"kind": "long_cash", "invested_min": 0.0, "invested_max": 1.0}, "min_variance")
    assert p["net"] == pytest.approx(0, abs=2e-3) and p["volatility"] == pytest.approx(0, abs=1e-4)   # solver precision


def test_benchmark_relative_tracking_error_budget_and_active_limits():
    b = np.array([0.3, 0.1, 0.1, 0.1, 0.3, 0.1])
    spec = {"kind": "benchmark_relative", "benchmark": b.tolist(), "active_limit": 0.10}
    p = opt(spec, "target_volatility", target=0.02)
    w = np.array(p["weights"])
    te = np.sqrt((w - b) @ COV @ (w - b))
    assert p["tracking_error"] == pytest.approx(te) and te <= 0.02 + 1e-6
    assert np.abs(w - b).max() <= 0.10 + 1e-6 and p["active_return"] > 0
    assert opt(spec, "min_variance")["tracking_error"] == pytest.approx(0, abs=1e-5)  # least TE = hold the benchmark
    ir = opt(spec, "max_sharpe")
    assert ir["information_ratio"] >= p["information_ratio"] - 1e-3


def test_surplus_mandate_hedges_the_liability():
    # liability proxied by the IG corporate bond index (column 4), fully funded
    p = opt({"kind": "surplus", "liability": 4, "funding_ratio": 1.0}, "min_variance")
    assert p["weights"][4] == pytest.approx(1, abs=2e-3) and p["tracking_error"] == pytest.approx(0, abs=1e-4)
    over = opt({"kind": "surplus", "liability": 4, "funding_ratio": 1.25}, "min_variance")
    # over-funded: hedge 1/F = 80% of assets, and the 20% surplus sits in the lowest-risk mix
    gmv = opt("long_only", "min_variance")
    assert over["weights"][4] >= 0.8 - 1e-3
    assert over["tracking_error"] == pytest.approx(0.2 * gmv["volatility"], abs=1e-4)


def test_turnover_limit_keeps_the_portfolio_close_to_current():
    cur = np.full(N, 1 / N)
    p = opt({"kind": "long_only", "current": cur.tolist(), "turnover_max": 0.2}, "max_sharpe")
    assert np.abs(np.array(p["weights"]) - cur).sum() <= 0.2 + 1e-5 and p["turnover"] <= 0.2 + 1e-5


def test_risk_parity_and_negative_limits_are_rejected_where_they_do_not_apply():
    with pytest.raises(optimize.Infeasible, match="long-only"):
        opt({"kind": "long_short"}, "risk_parity")
    with pytest.raises(optimize.Infeasible, match="short positions"):
        optimize.optimise(MU, COV, "min_variance", constraints={"lower": [-0.1] * N})


def test_backtest_with_cash_leverage_and_shorts():
    r = R[:60]
    cash_only, _ = performance.portfolio_returns(r, np.zeros(N), ppy=12, normalise=False, cash_rate=0.03)
    np.testing.assert_allclose(cash_only, (1.03) ** (1 / 12) - 1)
    two_x, _ = performance.portfolio_returns(r[:, [0]], [2.0], ppy=12, normalise=False)
    np.testing.assert_allclose(two_x, 2 * r[:, 0])                    # rebalanced 2x, free borrowing
    w = np.array([0.5, -0.2, 0.3, 0.1, 0.2, 0.1])                    # sums to 1 with a short
    ls, _ = performance.portfolio_returns(r, w, ppy=12, normalise=False)
    np.testing.assert_allclose(ls, r @ w)                             # rebalanced every period, no costs
    costly, _ = performance.portfolio_returns(r, w, ppy=12, normalise=False, short_cost=0.12)
    np.testing.assert_allclose(ls - costly, 0.2 * 0.01, atol=1e-12)   # 12% a year on 20% short = 0.2% a month


def test_simulation_and_spending_include_the_cash_line():
    zero = risk.monte_carlo(np.zeros(N), MU, COV, ppy=12, years=1, trials=200, extra_return=0.03)
    assert zero["median_ann_return"] == pytest.approx((1 + 0.03 / 12) ** 12 - 1, rel=1e-9) and zero["prob_loss"] == 0
    s = spending.simulate(np.zeros(N), MU, COV, years=5, trials=50, rate=0.0, inflation=0.0, inflation_vol=0.0,
                          extra_return=0.03, normalise=False)
    assert s["median_terminal_real"] == pytest.approx(100 * 1.03 ** 5, rel=1e-9)


def test_risk_budget_caps_tracking_error_for_every_objective():
    b = [0.3, 0.1, 0.1, 0.1, 0.3, 0.1]
    spec = {"kind": "benchmark_relative", "benchmark": b, "risk_budget": 0.03}
    for objective in ("max_return", "max_sharpe", "min_cvar"):
        p = opt(spec, objective)
        assert p["tracking_error"] <= 0.03 + 1e-5, objective
    assert opt(spec, "max_return")["tracking_error"] == pytest.approx(0.03, abs=1e-4)   # the budget binds
    front = optimize.efficient_frontier(MU, COV, rf=RF, mandate=md.parse(spec, N), points=15)["points"]
    assert max(p["tracking_error"] for p in front) <= 0.03 + 1e-5


def test_stress_windows_reach_2026_and_detect_new_drawdowns():
    dates = pd.bdate_range("2007-01-02", "2026-10-14")
    rng = np.random.default_rng(0)
    r = rng.normal(0.0003, 0.008, len(dates))
    names = [w["name"] for w in risk.suggest_windows(dates, r, 252)]
    for expected in ("2025 tariff shock", "Aug 2024 yen carry-trade unwind", "Q1 2026 equity sell-off", "Mid-2026 emerging-market sell-off"):
        assert expected in names
    short = risk.suggest_windows(pd.bdate_range("2008-01-02", "2026-10-14"), r[-4900:][:len(pd.bdate_range("2008-01-02", "2026-10-14"))], 252)
    assert any(n["name"].startswith("Global financial crisis (partial") for n in short)      # mostly covered: kept, trimmed
    # a fresh 20% fall after the last named crisis is found automatically and flagged as ongoing
    d2 = pd.bdate_range("2026-01-02", "2027-03-31")
    r2 = np.full(len(d2), 0.0004)
    r2[-40:] = -0.0056
    eps = risk.drawdown_episodes(d2, r2, 0.1)
    assert eps[0]["ongoing"] and eps[0]["depth"] < -0.19
    assert any(w["name"].startswith("Current drawdown") for w in risk.suggest_windows(d2, r2, 252))
