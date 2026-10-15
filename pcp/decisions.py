"""Rebalancing decision log and the between-quarter drift check.

Decisions are appended to data/decisions/rebalance_log.jsonl (one JSON object per line). The weekly data job
calls drift_check() to estimate how the last recorded holdings have drifted with market moves since then.
Only numpy and pandas are needed, so the scheduled task can run this with the system Python.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

import numpy as np
import pandas as pd

from .engine import rebalance

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "decisions" / "rebalance_log.jsonl"
ALERT = ROOT / "data" / "decisions" / "drift_alert.json"


def append(entry: dict) -> dict:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {"recorded_at": dt.datetime.now().isoformat(timespec="seconds"), **entry}
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def read_all() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line.strip()]


def growth_since(levels: pd.DataFrame, since: str) -> tuple[pd.Series, str, str]:
    """Value now / value at the first date on or after `since`, per column of a price-level table."""
    after = levels[levels.index >= pd.Timestamp(since)]
    if len(after) < 1:
        raise ValueError("No prices after the decision date yet.")
    return after.iloc[-1] / after.iloc[0], str(after.index[0].date()), str(after.index[-1].date())


def drift_from_last(levels: pd.DataFrame) -> dict:
    """Estimated drift of the last recorded decision's post-trade holdings, using price moves since then."""
    log = read_all()
    if not log:
        return {"available": False, "message": "No rebalancing decision has been recorded yet."}
    last = log[-1]
    assets = last["assets"]
    missing = [a for a in assets if a not in levels.columns]
    if missing:
        return {"available": False, "message": f"The data no longer has: {', '.join(missing)}."}
    g, d0, d1 = growth_since(levels[assets], last["decision_date"])
    weights = rebalance.drifted(last["post"], g.values)
    p = rebalance.plan(weights, last["target"], last["band"], last.get("policy", "breached_to_target"))
    return {"available": True, "decision_date": last["decision_date"], "from": d0, "to": d1, "assets": assets,
            "target_name": last.get("target_name", ""), "estimated_weights": weights.tolist(), "growth": g.values.tolist(),
            "n_breached": p["n_breached"], "breached_assets": [a for a, b in zip(assets, p["breached"]) if b],
            "drift": p["drift"], "band": p["band"], "target": p["target"],
            "message": (f"{p['n_breached']} holding(s) have drifted outside their band since {last['decision_date']}: "
                        f"{', '.join(a for a, b in zip(assets, p['breached']) if b)}.") if p["n_breached"]
                       else f"All holdings are still inside their bands (estimated to {d1})."}


def drift_check(csv_path, log=print) -> dict | None:
    """Run by the weekly update: refresh data/decisions/drift_alert.json from the latest price file."""
    levels = pd.read_csv(csv_path, header=1, index_col=0, parse_dates=True)
    result = drift_from_last(levels)
    if not result["available"]:
        log(f"Drift check skipped: {result['message']}")
        return None
    result["checked_at"] = dt.datetime.now().isoformat(timespec="seconds")
    ALERT.parent.mkdir(parents=True, exist_ok=True)
    ALERT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    log(("WARNING " if result["n_breached"] else "") + "Drift check: " + result["message"])
    return result
