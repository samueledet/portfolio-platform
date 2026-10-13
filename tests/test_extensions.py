"""Tests for the methods beyond the course workbooks: views, CVaR, attribution, currency, spending."""
import json
import pathlib

import numpy as np
import pytest

from pcp.engine import attribution, currency, data, optimize, performance, spending, stats

ROOT = pathlib.Path(__file__).parent.parent
u4 = json.loads((ROOT / "tests/fixtures/unit4.json").read_text(encoding="utf-8"))
u6 = json.loads((ROOT / "tests/fixtures/unit6.json").read_text(encoding="utf-8"))
R = np.array(u4["indexReturns"])
SAA, EW, B6040 = np.array(u4["saa"]["w"]), np.full(6, 1 / 6), np.array([0.6, 0, 0, 0, 0.4, 0])


# ------------------------------------------------------------------ assumptions
def test_blend_source_matches_unit4_average():
    def am(k):
        return {"name": k, "mu": [c[f"{k}Ret"] for c in u4["cme"]], "vol": [c[f"{k}Vol"] for c in u4["cme"]],
                "corr": u4["corr"][k]}
    a = stats.resolve_assumptions(R, 12, {"source": "blend", "sources": [am("am1"), am("am2")],
                                         "return_multiplier": 1.1, "vol_multiplier": 0.9})
    np.testing.assert_allclose(a["mu"], [c["optRet"] for c in u4["cme"]], atol=1e-8)
    np.testing.assert_allclose(a["cov"], u4["covOpt"], atol=1e-7)


def test_inconsistent_correlations_are_repaired_and_reported():
    bad = np.array([[1, 0.9, -0.9], [0.9, 1, 0.9], [-0.9, 0.9, 1.0]])
    fixed, note = stats.repair_correlation(bad)
    assert note and np.linalg.eigvalsh(fixed).min() > 0 and np.allclose(np.diag(fixed), 1)
    assert stats.repair_correlation(np.eye(3))[1] is None
    with pytest.raises(ValueError, match="symmetric"):
        stats.repair_correlation(np.array([[1, 0.2], [0.5, 1.0]]))


def test_black_litterman_prior_and_views():
    cov = stats.covariance(R, 12)
    none = stats.black_litterman(cov, EW, [], rf=0.02)
    np.testing.assert_allclose(none["posterior"], 0.02 + 2.5 * cov @ EW)          # no views -> equilibrium
    sure = stats.black_litterman(cov, EW, [{"type": "absolute", "asset": 0, "value": 0.10, "confidence": 1.0}], rf=0.02)
    assert sure["posterior"][0] == pytest.approx(0.10, abs=1e-6)                   # a certain view is honoured
    half = stats.black_litterman(cov, EW, [{"type": "absolute", "asset": 0, "value": 0.10, "confidence": 0.5}], rf=0.02)
    assert min(none["posterior"][0], 0.10) < half["posterior"][0] < max(none["posterior"][0], 0.10)
    rel = stats.black_litterman(cov, EW, [{"type": "relative", "long": 5, "short": 0, "value": 0.05, "confidence": 1.0}])
    assert rel["posterior"][5] - rel["posterior"][0] == pytest.approx(0.05, abs=1e-6)
    a = stats.resolve_assumptions(R, 12, {"source": "black_litterman", "market_weights": EW.tolist(),
                                         "views": [{"type": "absolute", "asset": 2, "value": 0.12, "confidence": 0.8}]}, rf=0.02)
    assert a["mu"][2] > a["prior_mu"][2]                                          # view above the prior lifts the estimate


# ------------------------------------------------------------------ CVaR optimisation
def test_min_cvar_beats_min_variance_on_tail_loss():
    a = stats.resolve_assumptions(R, 12, None)
    mc = optimize.optimise(a["mu"], a["cov"], "min_cvar", scenarios=R, beta=0.95)
    mv = optimize.optimise(a["mu"], a["cov"], "min_variance")

    def tail(p):
        return optimize.cvar(-(R @ np.array(p["weights"])), 0.95)
    assert tail(mc) <= tail(mv) + 1e-7 and mc["period_cvar"] == pytest.approx(tail(mc))
    assert sum(mc["weights"]) == pytest.approx(1) and min(mc["weights"]) >= -1e-9
    with_target = optimize.optimise(a["mu"], a["cov"], "min_cvar", scenarios=R, target=0.07)
    assert with_target["exp_return"] >= 0.07 - 1e-6 and tail(with_target) >= tail(mc) - 1e-7


# ------------------------------------------------------------------ attribution
def test_attribution_adds_up_exactly():
    win = R[-120:]
    out = attribution.attribute(win, SAA, B6040)
    rp, _ = performance.portfolio_returns(win, SAA, ppy=12)
    rb, _ = performance.portfolio_returns(win, B6040, ppy=12)
    assert out["total_return"] == pytest.approx(np.prod(1 + rp) - 1)
    assert sum(out["contribution"]) == pytest.approx(out["total_return"], abs=1e-12)
    assert out["active_return"] == pytest.approx(np.prod(1 + rp) - np.prod(1 + rb))
    assert sum(out["allocation_effect"]) == pytest.approx(out["active_return"], abs=1e-10)
    same = attribution.attribute(win, SAA, SAA)
    assert np.allclose(same["allocation_effect"], 0) and same["active_return"] == pytest.approx(0)


# ------------------------------------------------------------------ currency hedge
def test_currency_hedge_on_unit6_carve_out():
    ds = data.load_table((ROOT / "sample_data/uk_equity_fund_gbpusd.csv").read_bytes(), "fx.csv")
    assert ds.kind == "prices" and ds.returns.shape == (90, 2)
    fx = u6["fx"]
    summary = dict(fx["summary"])
    h = currency.hedge_analysis(ds.returns[:, 0], ds.returns[:, 1], ppy=252, hedge_ratio=0.5,
                                base_rate=fx["rates"]["usd"], foreign_rate=fx["rates"]["gbp"])
    m = h["metrics"]
    assert m["Local currency"]["cumulative"] == pytest.approx(summary["UK Equity Fund return in GBP (local)"], abs=1e-6)
    assert m["Currency"]["cumulative"] == pytest.approx(summary["Currency return: GBP vs USD"], abs=1e-6)
    assert m["Unhedged"]["cumulative"] == pytest.approx(summary["Unhedged return in USD"], abs=1e-6)
    assert m["Unhedged"]["volatility"] == pytest.approx(summary["Annualised volatility: unhedged USD"], abs=1e-5)
    # the workbook hedges a fixed USD notional with futures; a proportional forward hedge lands close to it
    assert m["Hedged"]["cumulative"] == pytest.approx(summary["50% hedged return in USD"], abs=0.004)
    assert m["Hedged"]["cumulative"] > m["Unhedged"]["cumulative"]            # GBP fell, so the hedge helped
    full = currency.hedge_analysis(ds.returns[:, 0], ds.returns[:, 1], ppy=252, hedge_ratio=1.0)
    rl, f = ds.returns[:, 0], ds.returns[:, 1]
    np.testing.assert_allclose(full["wealth"]["Hedged"][-1], np.prod((1 + rl) * (1 + f) - f))


# ------------------------------------------------------------------ spending policy
def test_spending_deterministic_case_matches_hand_calculation():
    # one asset, no volatility, no inflation: value_t = value_{t-1} x 1.05 - 3% x value_{t-1}
    res = spending.simulate([1.0], [0.05], [[0.0]], years=10, trials=50, rule="percent_of_value", rate=0.03,
                            inflation=0.0, inflation_vol=0.0)
    assert res["median_terminal_real"] == pytest.approx(100 * 1.02 ** 10, rel=1e-9)
    assert res["prob_preserve"] == 1.0 and res["first_year_spending"] == pytest.approx(3.0)
    fixed = spending.simulate([1.0], [0.05], [[0.0]], years=5, trials=10, rule="fixed_real", rate=0.04,
                              inflation=0.02, inflation_vol=0.0)
    np.testing.assert_allclose(fixed["spending_fan"]["values"][2], [4.0] * 5)      # constant in real terms


def test_spending_rules_behave_sensibly():
    a = stats.resolve_assumptions(R, 12, None)

    def run(**k):
        return spending.simulate(EW, a["mu"], a["cov"], years=30, trials=3000, seed=3, **k)
    low, high = run(rule="percent_of_value", rate=0.03), run(rule="percent_of_value", rate=0.06)
    assert low["prob_preserve"] > high["prob_preserve"]                          # spending more erodes the fund
    pct = run(rule="percent_of_value", rate=0.04)
    smooth, hybrid = run(rule="smoothed", rate=0.04, smoothing_years=5), run(rule="hybrid", rate=0.04)
    assert smooth["spending_volatility"] < pct["spending_volatility"]           # smoothing steadies the payout
    assert hybrid["spending_volatility"] < pct["spending_volatility"]
    assert run(rule="smoothed", rate=0.04) == run(rule="smoothed", rate=0.04)   # seeded
    grid = spending.compare_rates(EW, a["mu"], a["cov"], [0.03, 0.04, 0.05], years=20, trials=1000, rule="smoothed")
    probs = [g["prob_preserve"] for g in grid]
    assert probs == sorted(probs, reverse=True)
    with pytest.raises(ValueError, match="Spending rule"):
        run(rule="whatever")
