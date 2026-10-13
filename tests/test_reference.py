"""Reference tests: with the course data loaded, the engine must reproduce the completed Excel workbooks.

Fixtures in tests/fixtures are the values exported from Unit_2..6 - Assignment_Completed.xlsx.
"""
import json
import pathlib

import numpy as np
import pytest

from pcp.engine import data, optimize, performance, risk, stats

FIX = pathlib.Path(__file__).parent / "fixtures"
SAMPLES = pathlib.Path(__file__).parent.parent / "sample_data"
load = lambda name: json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def etfs():
    return data.load_table((SAMPLES / "course_etfs.csv").read_bytes(), "course_etfs.csv")


# ------------------------------------------------------------------ data loading
def test_upload_detects_daily_prices(etfs):
    assert etfs.kind == "prices" and etfs.frequency == "daily" and etfs.periods_per_year == 252
    assert etfs.assets == ["SABE", "WPETF", "KUSIG", "LPDM", "WPREI", "LPEM"]
    assert etfs.returns.shape == (1508, 6)          # 1,509 prices -> 1,508 returns


def test_upload_detects_monthly_returns():
    ds = data.load_table((SAMPLES / "index_returns_monthly.csv").read_bytes(), "index_returns_monthly.csv")
    assert ds.kind == "returns" and ds.frequency == "monthly" and ds.returns.shape == (215, 6)


def test_upload_reads_two_header_rows_and_excel_layout():
    csv = b"Asset Class,Bonds,Equity\nDate,AAA,BBB\n" + b"\n".join(
        f"2024-{m:02d}-28,{100 + m},{50 + 2 * m}".encode() for m in range(1, 13)) + b"\n2025-01-31,113,76\n"
    ds = data.load_table(csv, "two_headers.csv")
    assert ds.assets == ["AAA", "BBB"] and ds.names == ["Bonds", "Equity"] and ds.kind == "prices"


# ------------------------------------------------------------------ Unit 2 / 3: statistics and Solver
def test_unit3_inputs_match_workbook(etfs):
    u3 = load("unit3.json")
    a = stats.resolve_assumptions(etfs.returns, 252, {"method": "arithmetic"})
    np.testing.assert_allclose(a["mu"], u3["er"], atol=1e-6)
    np.testing.assert_allclose(a["vol"], u3["vol"], atol=1e-6)
    np.testing.assert_allclose(a["cov"], u3["covA"], atol=1e-7)


def test_unit3_solver_portfolios(etfs):
    u3 = load("unit3.json")
    a = stats.resolve_assumptions(etfs.returns, 252, None)
    solver = {o["sheet"]: o for o in u3["optimisations"]}
    mv = optimize.optimise(a["mu"], a["cov"], "min_variance", rf=u3["rf"])
    assert mv["volatility"] == pytest.approx(solver["Min Risk"]["vol"], abs=2e-5)
    assert mv["volatility"] <= solver["Min Risk"]["vol"] + 1e-7          # at least as good as Solver
    np.testing.assert_allclose(mv["weights"], solver["Min Risk"]["w"], atol=5e-3)
    mr = optimize.optimise(a["mu"], a["cov"], "max_return")
    assert mr["exp_return"] == pytest.approx(solver["Max Return"]["ret"], abs=1e-6)
    t12 = optimize.optimise(a["mu"], a["cov"], "target_volatility", target=0.12, rf=u3["rf"])
    assert t12["exp_return"] == pytest.approx(solver["Target Vol 12%"]["ret"], abs=2e-5)
    assert t12["volatility"] == pytest.approx(0.12, abs=1e-5)
    np.testing.assert_allclose(t12["weights"], solver["Target Vol 12%"]["w"], atol=2e-3)


def test_max_sharpe_beats_every_frontier_point(etfs):
    a = stats.resolve_assumptions(etfs.returns, 252, None)
    ms = optimize.optimise(a["mu"], a["cov"], "max_sharpe", rf=0.005)
    front = optimize.efficient_frontier(a["mu"], a["cov"], rf=0.005, points=30)["points"]
    assert ms["sharpe"] >= max(p["sharpe"] for p in front) - 1e-4


# ------------------------------------------------------------------ Unit 4: CMEs, SAA, backtest
def test_unit4_optimistic_saa():
    u4 = load("unit4.json")
    mu = np.array([c["optRet"] for c in u4["cme"]])
    saa = optimize.optimise(mu, np.array(u4["covOpt"]), "target_volatility", target=u4["saa"]["maxVol"],
                            constraints={"lower": [u4["saa"]["minW"]] * 6})
    np.testing.assert_allclose(saa["weights"], u4["saa"]["w"], atol=1e-4)
    assert saa["exp_return"] == pytest.approx(u4["saa"]["ret"], abs=1e-6)
    assert saa["volatility"] == pytest.approx(u4["saa"]["pvol"], abs=1e-6)


def test_unit4_blend_and_tilt_reproduce_optimistic_cmes():
    u4 = load("unit4.json")
    am = lambda k: {"mu": [c[f"{k}Ret"] for c in u4["cme"]], "vol": [c[f"{k}Vol"] for c in u4["cme"]],
                    "corr": u4["corr"][k]}
    avg = stats.blend_assumptions([am("am1"), am("am2")])
    np.testing.assert_allclose(avg["mu"] * (1 + u4["uplift"]), [c["optRet"] for c in u4["cme"]], atol=1e-8)
    cov = stats.cov_from_vol_corr(avg["vol"] * (1 - u4["volCut"]), avg["corr"])
    np.testing.assert_allclose(cov, u4["covOpt"], atol=1e-7)


@pytest.mark.parametrize("window", ["3", "5", "10"])
def test_unit4_backtest_tables(window):
    u4 = load("unit4.json")
    w = u4["backtest"]["windows"][window]
    rets = np.array(u4["indexReturns"])[w["start"]:w["end"] + 1]
    rf = u4["results"][window]["rf"]
    rows = u4["results"][window]["rows"]
    for weights, row in ((u4["saa"]["w"], rows[0]), ([1 / 6] * 6, rows[1]), ([0.6, 0, 0, 0, 0.4, 0], rows[2])):
        series, _ = performance.portfolio_returns(rets, weights, ppy=12, rebalance="every_period")
        m = performance.metrics(series, ppy=12, rf=rf)
        assert m["ann_return"] == pytest.approx(row["ann"], abs=1e-7)
        assert m["volatility"] == pytest.approx(row["vol"], abs=1e-7)
        assert m["sharpe"] == pytest.approx(row["sharpe"], abs=1e-6)
        assert m["cumulative"] == pytest.approx(row["cum"], abs=1e-7)
        assert m["worst"] == pytest.approx(row["worst"], abs=1e-7)


# ------------------------------------------------------------------ Unit 5: SAA, sensitivity, Monte Carlo
def test_unit5_saa_and_sensitivity():
    u5 = load("unit5.json")
    cov = stats.cov_from_vol_corr(np.array(u5["vol"]), np.array(u5["corr"]))
    saa = optimize.optimise(u5["er"], cov, "target_volatility", target=u5["saa"]["maxVol"],
                            constraints={"lower": [u5["saa"]["minW"]] * 6})
    np.testing.assert_allclose(saa["weights"], u5["saa"]["w"], atol=2e-4)
    assert saa["exp_return"] == pytest.approx(u5["saa"]["ret"], abs=1e-6)
    rows = u5["sensitivity"]["rows"]
    s = risk.shock_sensitivity([r["w"] for r in rows], [r["cmeRet"] for r in rows], [r["shockRet"] for r in rows])
    assert s["break_even"] == pytest.approx(u5["sensitivity"]["breakeven"], abs=1e-5)
    assert s["shock_return"] == pytest.approx(u5["sensitivity"]["summary"][0][2], abs=1e-7)


def test_unit5_monte_carlo_centres_on_scenario_inputs():
    u5 = load("unit5.json")
    for sc in u5["scenarios"]:
        mu, cov = np.array(sc["mMean"]) * 12, np.array(sc["cov"]) * 12
        mc = risk.monte_carlo(u5["saa"]["w"], mu, cov, ppy=12, years=sc["mc"]["months"] / 12, trials=20000, seed=1)
        w = np.array(u5["saa"]["w"])
        assert float(w @ mu) == pytest.approx(sc["mc"]["detRet"], abs=1e-6)          # deterministic scenario return
        assert mc["mean_volatility"] == pytest.approx(sc["mc"]["avgVol"], abs=0.004)  # Excel: 1,000 trials


# ------------------------------------------------------------------ Unit 6: performance evaluation
def test_unit6_performance_metrics():
    u6 = load("unit6.json")
    key = {r[0]: r for r in u6["report"]["keyFigures"]}
    for col, series in ((1, u6["monthly"]["saa"]), (2, u6["monthly"]["taa"])):
        m = performance.metrics(np.array(series), ppy=12, rf=0.0235)
        assert m["mean_ann_return"] == pytest.approx(key["Annualised mean return"][col], abs=1e-7)
        assert m["volatility"] == pytest.approx(key["Annualised volatility"][col], abs=1e-7)
        assert m["downside_deviation"] == pytest.approx(key["Downside deviation (annualised)"][col], abs=1e-6)
        assert m["sortino"] == pytest.approx(key["Sortino ratio (MAR 2.35%, consistent)"][col], abs=1e-5)
        assert m["skew"] == pytest.approx(key["Skewness"][col], abs=1e-6)
        assert m["kurtosis"] == pytest.approx(key["Excess kurtosis"][col], abs=1e-5)
        assert m["cumulative"] == pytest.approx(key["Cumulative return (15 months)"][col], abs=1e-7)


# ------------------------------------------------------------------ properties that must always hold
@pytest.mark.parametrize("objective", ["min_variance", "max_return", "max_sharpe"])
def test_constraints_always_hold(etfs, objective):
    a = stats.resolve_assumptions(etfs.returns, 252, None)
    cons = {"lower": [0.05] * 6, "upper": [0.40] * 6, "groups": [{"assets": [1, 3, 5], "max": 0.5}]}
    w = np.array(optimize.optimise(a["mu"], a["cov"], objective, rf=0.0, constraints=cons)["weights"])
    assert w.sum() == pytest.approx(1) and (w >= 0.05 - 1e-6).all() and (w <= 0.40 + 1e-6).all()
    assert w[[1, 3, 5]].sum() <= 0.5 + 1e-6


def test_infeasible_constraints_explain_themselves(etfs):
    a = stats.resolve_assumptions(etfs.returns, 252, None)
    with pytest.raises(optimize.Infeasible, match="more than 100%"):
        optimize.optimise(a["mu"], a["cov"], "min_variance", constraints={"lower": [0.2] * 6})
    with pytest.raises(optimize.Infeasible, match="below the minimum"):
        optimize.optimise(a["mu"], a["cov"], "target_volatility", target=0.01)


def test_rebalancing_and_seeded_simulation(etfs):
    w = [1 / 6] * 6
    every, _ = performance.portfolio_returns(etfs.returns, w, ppy=252, rebalance="every_period")
    np.testing.assert_allclose(every, etfs.returns @ np.array(w))
    hold, turnover = performance.portfolio_returns(etfs.returns, w, ppy=252, rebalance="never")
    assert turnover == 0 and np.prod(1 + hold) == pytest.approx(np.mean(np.prod(1 + etfs.returns, axis=0)))
    a = stats.resolve_assumptions(etfs.returns, 252, None)
    run = lambda m: risk.monte_carlo(w, a["mu"], a["cov"], ppy=252, years=1, trials=500, seed=7, method=m,
                                     history=etfs.returns)
    assert run("normal") == run("normal")
    assert run("bootstrap")["trials"] == 500 and run("student_t")["prob_loss"] >= 0
