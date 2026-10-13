"""HTTP API over the analytics engine, plus the static web app.

Prototype storage: uploaded datasets are kept in memory for the life of the server process.
"""
from __future__ import annotations

import io
import pathlib
import uuid
from typing import Any

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import data, optimize, performance, risk, stats

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = {
    "etfs": ("course_etfs.csv", "Six ETFs, daily prices 2012-2017"),
    "indices": ("index_returns_monthly.csv", "Six asset-class indices, monthly returns 2000-2017"),
}
MAX_UPLOAD = 25 * 1024 * 1024

app = FastAPI(title="Portfolio Construction Platform", version="0.1.0")
STORE: dict[str, dict] = {}


# ------------------------------------------------------------------ helpers
def clean(obj: Any) -> Any:
    """Make engine output JSON-safe (numpy types, NaN -> null)."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    if isinstance(obj, (np.floating, float)):
        return float(obj) if np.isfinite(obj) else None
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


@app.exception_handler(ValueError)
async def value_error(_, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


def get(dataset_id: str) -> data.Dataset:
    if dataset_id not in STORE:
        raise HTTPException(404, "Dataset not found. It may have been cleared when the server restarted; upload it again.")
    return STORE[dataset_id]["dataset"]


def summary(dataset_id: str) -> dict:
    rec = STORE[dataset_id]
    ds: data.Dataset = rec["dataset"]
    return {"id": dataset_id, "filename": rec["filename"], "assets": ds.assets, "names": ds.names,
            "kind": ds.kind, "frequency": ds.frequency, "periods_per_year": ds.periods_per_year,
            "observations": int(ds.returns.shape[0]), "start": str(ds.dates[0].date()),
            "end": str(ds.dates[-1].date()), "warnings": ds.warnings}


def register(content: bytes, filename: str, kind: str = "auto", frequency: str = "auto") -> dict:
    ds = data.load_table(content, filename, kind=kind, frequency=frequency)
    dataset_id = uuid.uuid4().hex[:12]
    STORE[dataset_id] = {"dataset": ds, "filename": filename}
    return summary(dataset_id)


def window(ds: data.Dataset, start: str | None, end: str | None):
    mask = data.slice_dates(ds, start, end)
    if mask.sum() < 12:
        raise ValueError("The chosen date range has fewer than 12 observations.")
    return ds.returns[mask], ds.dates[mask]


def thin(n: int, limit: int = 500) -> np.ndarray:
    """Indices that downsample a long series for charting, always keeping the last point."""
    if n <= limit:
        return np.arange(n)
    idx = np.unique(np.linspace(0, n - 1, limit).round().astype(int))
    return idx


def check_weights(weights: list[float], n: int) -> np.ndarray:
    w = np.asarray(weights, dtype=float)
    if w.shape != (n,):
        raise ValueError(f"Expected {n} weights, one per asset.")
    if abs(w.sum()) < 1e-9:
        raise ValueError("Weights sum to zero.")
    return w / w.sum()


# ------------------------------------------------------------------ request models
class Base(BaseModel):
    assumptions: dict | None = None
    rf: float = 0.0
    start: str | None = None
    end: str | None = None


class OptimiseIn(Base):
    objective: str = "max_sharpe"
    target: float | None = None
    constraints: dict | None = None


class FrontierIn(Base):
    constraints: dict | None = None
    points: int = Field(40, ge=5, le=200)


class Portfolio(BaseModel):
    name: str
    weights: list[float]


class BacktestIn(Base):
    portfolios: list[Portfolio]
    benchmark: str | None = None
    rebalance: str = "every_period"
    cost_bps: float = Field(0.0, ge=0, le=500)


class StressIn(Base):
    weights: list[float]
    windows: list[dict] | None = None
    shock_window: dict | None = None


class SimulateIn(Base):
    weights: list[float]
    years: float = Field(5.0, gt=0, le=50)
    trials: int = Field(5000, ge=100, le=100000)
    method: str = "normal"
    seed: int = 42
    df: int = Field(5, ge=3, le=60)


class ReportIn(BacktestIn):
    constraints: dict | None = None


# ------------------------------------------------------------------ datasets
@app.get("/api/health")
def health():
    return {"status": "ok", "datasets": len(STORE), "version": app.version}


@app.get("/api/samples")
def samples():
    return [{"key": k, "description": d} for k, (_, d) in SAMPLES.items()]


@app.post("/api/datasets/sample/{key}")
def load_sample(key: str):
    if key not in SAMPLES:
        raise HTTPException(404, "Unknown sample.")
    name = SAMPLES[key][0]
    return register((ROOT / "sample_data" / name).read_bytes(), name)


@app.post("/api/datasets")
async def upload(file: UploadFile = File(...), kind: str = Form("auto"), frequency: str = Form("auto")):
    content = await file.read()
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "File is larger than 25 MB.")
    if not (file.filename or "").lower().endswith((".csv", ".txt", ".xlsx", ".xlsm", ".xls")):
        raise ValueError("Upload a CSV or Excel file.")
    return register(content, file.filename, kind, frequency)


@app.get("/api/datasets/{dataset_id}")
def dataset(dataset_id: str):
    get(dataset_id)
    return summary(dataset_id)


# ------------------------------------------------------------------ analyses
@app.post("/api/datasets/{dataset_id}/statistics")
def statistics(dataset_id: str, body: Base):
    ds = get(dataset_id)
    r, dates = window(ds, body.start, body.end)
    spec = body.assumptions or {}
    a = stats.resolve_assumptions(r, ds.periods_per_year, spec)
    hist = stats.resolve_assumptions(r, ds.periods_per_year, {"method": spec.get("method", "arithmetic"),
                                                              "covariance": spec.get("covariance")})
    idx = thin(len(r) + 1)
    growth = stats.growth(r)[idx]
    labels = [str(dates[0].date())] + [str(d.date()) for d in dates]
    return clean({
        "assets": stats.asset_statistics(r, ds.periods_per_year, body.rf, spec.get("method", "arithmetic")),
        "assumptions": {k: a[k] for k in ("mu", "vol", "corr", "cov", "shrinkage")},
        "historical": {k: hist[k] for k in ("mu", "vol", "corr")},
        "growth": {"dates": [labels[i] for i in idx], "values": growth.T},
        "histograms": [stats.histogram(r[:, i]) for i in range(ds.n_assets)],
        "observations": int(len(r)), "start": str(dates[0].date()), "end": str(dates[-1].date()),
    })


@app.post("/api/datasets/{dataset_id}/optimise")
def optimise(dataset_id: str, body: OptimiseIn):
    ds = get(dataset_id)
    r, _ = window(ds, body.start, body.end)
    a = stats.resolve_assumptions(r, ds.periods_per_year, body.assumptions)
    return clean(optimize.optimise(a["mu"], a["cov"], body.objective, rf=body.rf, target=body.target,
                                   constraints=body.constraints))


@app.post("/api/datasets/{dataset_id}/frontier")
def frontier(dataset_id: str, body: FrontierIn):
    ds = get(dataset_id)
    r, _ = window(ds, body.start, body.end)
    a = stats.resolve_assumptions(r, ds.periods_per_year, body.assumptions)
    mu, cov = a["mu"], a["cov"]
    front = optimize.efficient_frontier(mu, cov, rf=body.rf, constraints=body.constraints, points=body.points)
    key = {"Minimum variance": front["min_variance"], "Maximum return": front["max_return"]}
    notes = []
    for label, objective in (("Maximum Sharpe", "max_sharpe"), ("Risk parity", "risk_parity")):
        try:
            key[label] = optimize.optimise(mu, cov, objective, rf=body.rf, constraints=body.constraints)
        except optimize.Infeasible as exc:
            notes.append(f"{label}: {exc}")
    n = ds.n_assets
    key["Equal weight"] = optimize.describe(np.full(n, 1 / n), mu, cov, body.rf)
    return clean({"points": front["points"], "portfolios": key, "notes": notes,
                  "assets": [{"exp_return": mu[i], "volatility": a["vol"][i]} for i in range(n)]})


@app.post("/api/datasets/{dataset_id}/backtest")
def backtest(dataset_id: str, body: BacktestIn):
    ds = get(dataset_id)
    r, dates = window(ds, body.start, body.end)
    ppy = ds.periods_per_year
    series = {}
    for p in body.portfolios:
        series[p.name] = performance.portfolio_returns(
            r, check_weights(p.weights, ds.n_assets), ppy=ppy, rebalance=body.rebalance, cost_bps=body.cost_bps)
    if body.benchmark and body.benchmark not in series:
        raise ValueError("The benchmark must be one of the portfolios in the request.")
    bench = series[body.benchmark][0] if body.benchmark else None
    idx = thin(len(r) + 1)
    labels = [str(dates[0].date())] + [str(d.date()) for d in dates]
    out = []
    for name, (s, turnover) in series.items():
        wealth, dd = performance.wealth_and_drawdown(s)
        m = performance.metrics(s, ppy=ppy, rf=body.rf, benchmark=bench if name != body.benchmark else None)
        out.append({"name": name, "metrics": {**m, "turnover": turnover}, "wealth": wealth[idx], "drawdown": dd[idx]})
    return clean({"dates": [labels[i] for i in idx], "portfolios": out, "observations": int(len(r)),
                  "start": str(dates[0].date()), "end": str(dates[-1].date())})


@app.post("/api/datasets/{dataset_id}/stress")
def stress(dataset_id: str, body: StressIn):
    ds = get(dataset_id)
    w = check_weights(body.weights, ds.n_assets)
    ppy = ds.periods_per_year
    windows = body.windows or risk.suggest_windows(ds.dates, ds.returns @ w, ppy)
    result = {"windows": risk.window_analysis(ds.returns, ds.dates, w, windows, ppy=ppy, rf=body.rf)}
    if body.shock_window:
        r, _ = window(ds, body.start, body.end)
        base = stats.resolve_assumptions(r, ppy, body.assumptions)["mu"]
        shock = risk.window_analysis(ds.returns, ds.dates, w, [body.shock_window], ppy=ppy)[0]
        if "error" in shock:
            raise ValueError(shock["error"])
        result["sensitivity"] = {**risk.shock_sensitivity(w, base, shock["asset_return"]),
                                 "base_mu": base, "shock_mu": shock["asset_return"], "window": body.shock_window}
    return clean(result)


@app.post("/api/datasets/{dataset_id}/simulate")
def simulate(dataset_id: str, body: SimulateIn):
    ds = get(dataset_id)
    r, _ = window(ds, body.start, body.end)
    a = stats.resolve_assumptions(r, ds.periods_per_year, body.assumptions)
    w = check_weights(body.weights, ds.n_assets)
    return clean(risk.monte_carlo(w, a["mu"], a["cov"], ppy=ds.periods_per_year, years=body.years, trials=body.trials,
                                  method=body.method, seed=body.seed, history=r, df=body.df, rf=body.rf))


@app.post("/api/datasets/{dataset_id}/report.xlsx")
def report(dataset_id: str, body: ReportIn):
    """Audit workbook: inputs, assumptions, portfolios and backtest results as plain values."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    ds = get(dataset_id)
    r, dates = window(ds, body.start, body.end)
    ppy = ds.periods_per_year
    a = stats.resolve_assumptions(r, ppy, body.assumptions)
    wb = Workbook()
    bold = Font(name="Arial", bold=True)

    def sheet(title, rows, first=False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        for row in rows:
            ws.append([None if isinstance(v, float) and not np.isfinite(v) else v for v in row])
        for cell in ws[1]:
            cell.font = bold
        ws.column_dimensions["A"].width = 34
        return ws

    info = summary(dataset_id)
    sheet("Inputs", [["Item", "Value"], ["Source file", info["filename"]], ["Data type", ds.kind],
                     ["Frequency", ds.frequency], ["Analysis start", str(dates[0].date())],
                     ["Analysis end", str(dates[-1].date())], ["Observations", int(len(r))],
                     ["Risk-free rate (annual)", body.rf], ["Assumption source", (body.assumptions or {}).get("source", "historical")],
                     ["Return multiplier", (body.assumptions or {}).get("return_multiplier", 1.0)],
                     ["Volatility multiplier", (body.assumptions or {}).get("vol_multiplier", 1.0)],
                     ["Rebalancing", body.rebalance], ["Trading cost (bps)", body.cost_bps],
                     ["Note", "Analytics output for review. Not investment advice."]], first=True)
    sheet("Assumptions", [["Asset", "Name", "Expected return", "Volatility", "Min weight", "Max weight"]] + [
        [ds.assets[i], ds.names[i], float(a["mu"][i]), float(a["vol"][i]),
         ((body.constraints or {}).get("lower") or [0.0] * ds.n_assets)[i],
         ((body.constraints or {}).get("upper") or [1.0] * ds.n_assets)[i]] for i in range(ds.n_assets)])
    sheet("Correlation", [[""] + ds.assets] + [[ds.assets[i]] + [float(x) for x in a["corr"][i]] for i in range(ds.n_assets)])
    sheet("Covariance", [[""] + ds.assets] + [[ds.assets[i]] + [float(x) for x in a["cov"][i]] for i in range(ds.n_assets)])
    rows = [["Portfolio"] + ds.assets + ["Expected return", "Volatility", "Sharpe"]]
    for p in body.portfolios:
        d = optimize.describe(check_weights(p.weights, ds.n_assets), a["mu"], a["cov"], body.rf)
        rows.append([p.name] + d["weights"] + [d["exp_return"], d["volatility"], d["sharpe"]])
    sheet("Portfolios", rows)
    bt = backtest(dataset_id, body)
    keys = ["ann_return", "volatility", "sharpe", "sortino", "max_drawdown", "cumulative", "worst", "best",
            "var_95", "cvar_95", "turnover", "active_return", "tracking_error", "information_ratio", "beta"]
    sheet("Backtest", [["Portfolio"] + keys] + [[p["name"]] + [p["metrics"].get(k) for k in keys] for p in bt["portfolios"]])
    sheet("Backtest paths", [["Date"] + [p["name"] for p in bt["portfolios"]]] + [
        [d] + [p["wealth"][i] for p in bt["portfolios"]] for i, d in enumerate(bt["dates"])])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": 'attachment; filename="portfolio_analysis.xlsx"'})


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
