"""Rebalancing rules: trades add up to zero, holdings end inside their bands, and no band breach means no trade."""
import numpy as np
import pandas as pd
import pytest

from pcp.engine import rebalance as rb

T = np.array([0.40, 0.30, 0.20, 0.10])
B = rb.bands(T, 0.02, 0.25)                      # [0.10, 0.075, 0.05, 0.025]


def test_bands_take_the_larger_of_absolute_and_relative():
    np.testing.assert_allclose(B, [0.10, 0.075, 0.05, 0.025])
    np.testing.assert_allclose(rb.bands([0.04], 0.02, 0.25), [0.02])


def test_no_breach_means_no_trades():
    c = np.array([0.45, 0.27, 0.18, 0.10])
    for policy in rb.POLICIES:
        p = rb.plan(c, T, B, policy)
        assert p["n_breached"] == 0 and p["turnover"] == 0 and "No trades" in p["action"]


@pytest.mark.parametrize("policy", list(rb.POLICIES))
def test_breach_is_fixed_and_trades_net_to_zero(policy):
    c = np.array([0.55, 0.22, 0.15, 0.08])         # equities ran up: first holding 15 points over target
    p = rb.plan(c, T, B, policy, balance=200_000, cost_bps=10)
    post = np.array(p["post"])
    assert p["breached"][0] and sum(p["trades"]) == pytest.approx(0, abs=1e-12)
    assert np.all(np.abs(post - T) <= B + 1e-9) and post.sum() == pytest.approx(1)
    assert sum(p["trade_amounts"]) == pytest.approx(0, abs=1e-6) and p["cost"] == pytest.approx(p["turnover"] * 2 * 0.001)


def test_band_edge_trades_least_and_all_to_target_trades_most():
    c = np.array([0.55, 0.22, 0.15, 0.08])
    t = {k: rb.plan(c, T, B, k)["turnover"] for k in rb.POLICIES}
    assert t["to_band_edge"] <= t["breached_to_target"] + 1e-12 <= t["all_to_target"] + 2e-12
    assert rb.plan(c, T, B, "to_band_edge")["post"][0] == pytest.approx(0.50)   # 40% target + 10-point band


def test_drifted_weights_follow_relative_growth():
    np.testing.assert_allclose(rb.drifted([0.5, 0.5], [1.2, 0.8]), [0.6, 0.4])


def test_invalid_input_is_explained():
    with pytest.raises(ValueError, match="long-only"):
        rb.plan([0.5, -0.1, 0.6], [0.3, 0.3, 0.4], [0.05] * 3)
    with pytest.raises(ValueError, match="policy"):
        rb.plan([0.5, 0.5], [0.5, 0.5], [0.05, 0.05], "sometimes")


# ------------------------------------------------------------------ API and decision log
from fastapi.testclient import TestClient  # noqa: E402

from pcp import decisions  # noqa: E402
from pcp.api import app  # noqa: E402

client = TestClient(app)


@pytest.fixture()
def tmp_log(tmp_path, monkeypatch):
    monkeypatch.setattr(decisions, "LOG", tmp_path / "log.jsonl")
    monkeypatch.setattr(decisions, "ALERT", tmp_path / "alert.json")
    return tmp_path


def test_rebalance_endpoint_record_and_drift(tmp_log):
    ds = client.post("/api/datasets/sample/indices").json()
    target = [0.3, 0.1, 0.1, 0.1, 0.3, 0.1]
    amounts = [90_000, 10_000, 10_000, 10_000, 70_000, 10_000]          # US Large Cap 45% vs 30% target
    res = client.post(f"/api/datasets/{ds['id']}/rebalance", json={"target": target, "current": amounts, "units": "amounts", "cost_bps": 5})
    assert res.status_code == 200, res.text
    p = res.json()
    assert p["balance"] == 200_000 and p["breached"][0] and sum(p["trade_amounts"]) == pytest.approx(0, abs=1e-6)
    assert p["risk"]["post"]["tracking_error"] < p["risk"]["current"]["tracking_error"]
    entry = {k: p[k] for k in ("target", "current", "post", "trades", "band", "policy", "balance")}
    rec = client.post("/api/rebalance/log", json={**entry, "dataset": "indices", "assets": ds["assets"], "target_name": "SAA",
                                                  "note": "Q4 review", "decision_date": "2015-01-02"}).json()
    assert rec["decision_date"] == "2015-01-02" and client.get("/api/rebalance/log").json()[0]["note"] == "Q4 review"
    csv = client.get("/api/rebalance/log.csv").text
    assert csv.count("\n") == 6 and "Q4 review" in csv
    drift = client.post(f"/api/datasets/{ds['id']}/rebalance/drifted").json()
    assert drift["available"] and drift["from"].startswith("2015-01") and sum(drift["estimated_weights"]) == pytest.approx(1)
    bad = client.post(f"/api/datasets/{ds['id']}/rebalance", json={"target": target, "current": [1, 2]})
    assert bad.status_code == 422 and "each of the 6 assets" in bad.json()["detail"]


def test_weekly_drift_check_writes_an_alert(tmp_log):
    dates = pd.bdate_range("2026-01-01", periods=60)
    levels = pd.DataFrame({"A": np.linspace(100, 160, 60), "B": np.full(60, 100.0)}, index=dates)   # A rallies 60%
    path = tmp_log / "prices.csv"
    with open(path, "w") as f:
        f.write('"Proxy","a","b"\n"Date","A","B"\n')
        for d, row in levels.iterrows():
            f.write(f"{d:%Y-%m-%d},{row.A},{row.B}\n")
    decisions.append({"assets": ["A", "B"], "target": [0.5, 0.5], "post": [0.5, 0.5], "band": [0.05, 0.05],
                      "policy": "breached_to_target", "decision_date": "2026-01-01"})
    res = decisions.drift_check(path, log=lambda m: None)
    assert res["n_breached"] == 2 and res["estimated_weights"][0] == pytest.approx(160 / 260)
    assert decisions.ALERT.exists()
