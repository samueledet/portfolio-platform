"""API tests: the full client journey through HTTP, on the sample data."""
import io

import pytest
from fastapi.testclient import TestClient

from pcp.api import app

client = TestClient(app)


@pytest.fixture(scope="module")
def ds():
    return client.post("/api/datasets/sample/indices").json()


def post(ds, path, **body):
    res = client.post(f"/api/datasets/{ds['id']}/{path}", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def test_upload_csv_and_reject_bad_files():
    csv = "Date,A,B\n" + "\n".join(f"2023-{m:02d}-28,{100 + m},{200 - m}" for m in range(1, 13)) + "\n2024-01-31,115,180\n"
    res = client.post("/api/datasets", files={"file": ("mine.csv", io.BytesIO(csv.encode()), "text/csv")})
    assert res.status_code == 200 and res.json()["assets"] == ["A", "B"] and res.json()["kind"] == "prices"
    bad = client.post("/api/datasets", files={"file": ("notes.pdf", io.BytesIO(b"x"), "application/pdf")})
    assert bad.status_code == 422 and "CSV or Excel" in bad.json()["detail"]
    empty = client.post("/api/datasets", files={"file": ("e.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")})
    assert empty.status_code == 422 and "date column" in empty.json()["detail"]


def test_statistics_and_custom_assumptions(ds):
    base = post(ds, "statistics", rf=0.01)
    assert len(base["assets"]) == 6 and len(base["growth"]["values"]) == 6
    tilted = post(ds, "statistics", assumptions={"return_multiplier": 1.1, "vol_multiplier": 0.9})
    assert tilted["assumptions"]["mu"][0] == pytest.approx(base["assumptions"]["mu"][0] * 1.1)
    assert tilted["assumptions"]["vol"][0] == pytest.approx(base["assumptions"]["vol"][0] * 0.9)


def test_optimise_frontier_and_infeasible_message(ds):
    cons = {"lower": [0.1] * 6, "upper": [0.5] * 6}
    p = post(ds, "optimise", objective="target_volatility", target=0.10, constraints=cons)
    assert sum(p["weights"]) == pytest.approx(1) and min(p["weights"]) >= 0.1 - 1e-6 and p["volatility"] <= 0.10 + 1e-6
    f = post(ds, "frontier", constraints=cons, rf=0.01)
    assert len(f["points"]) > 10 and "Maximum Sharpe" in f["portfolios"]
    vols = [q["volatility"] for q in f["points"]]
    assert vols == sorted(vols)
    bad = client.post(f"/api/datasets/{ds['id']}/optimise", json={"objective": "min_variance", "constraints": {"lower": [0.3] * 6}})
    assert bad.status_code == 422 and "more than 100%" in bad.json()["detail"]


def test_backtest_stress_simulate_and_report(ds):
    ports = [{"name": "SAA", "weights": [0.1, 0.1, 0.5, 0.1, 0.1, 0.1]}, {"name": "60/40", "weights": [0.6, 0, 0, 0, 0.4, 0]}]
    bt = post(ds, "backtest", portfolios=ports, benchmark="60/40", start="2008-01-01", end="2017-12-31", rf=0.006)
    saa = bt["portfolios"][0]["metrics"]
    assert saa["ann_return"] == pytest.approx(0.01118, abs=1e-5) and "tracking_error" in saa      # Unit 4, 10-year table
    st = post(ds, "stress", weights=ports[0]["weights"], shock_window={"start": "2014-07-01", "end": "2016-02-29"})
    assert any(w["name"] == "Global financial crisis" for w in st["windows"]) and st["sensitivity"]["severity"][0] == 0
    for method in ("normal", "student_t", "bootstrap"):
        mc = post(ds, "simulate", weights=ports[0]["weights"], years=3, trials=500, method=method)
        assert len(mc["fan"]["wealth"]) == 5 and 0 <= mc["prob_loss"] <= 1
    res = client.post(f"/api/datasets/{ds['id']}/report.xlsx", json={"portfolios": ports, "benchmark": "60/40"})
    assert res.status_code == 200 and res.content[:2] == b"PK"


def test_unknown_dataset_is_a_clear_404():
    res = client.post("/api/datasets/nope/statistics", json={})
    assert res.status_code == 404 and "upload it again" in res.json()["detail"]
