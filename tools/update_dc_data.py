"""Download the DC-plan asset classes and refresh data/dc_plan/dc_plan_daily.csv.

Usage:  python tools/update_dc_data.py [--config config/dc_plan.json]
Run weekly by Windows Task Scheduler through run_dc_update.ps1. The previous file is kept as a backup and is
only replaced when the new download passes its checks; every run is logged to data/dc_plan/update.log.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import shutil
import sys
import traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pcp import decisions, feeds  # noqa: E402
from pcp.engine import data  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=str(ROOT / "config" / "dc_plan.json"))
    args = ap.parse_args()
    config = json.loads(pathlib.Path(args.config).read_text(encoding="utf-8"))
    out = ROOT / config.get("output", "data/dc_plan/dc_plan_daily.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    log_path = out.parent / "update.log"

    def log(msg: str) -> None:
        line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    log(f"Update started ({len(config['assets'])} assets from {config.get('start')})")
    try:
        df, meta = feeds.build(config, log=log)
        tmp = out.with_suffix(".new.csv")
        feeds.write_csv(df, meta, tmp)
        check = data.load_table(tmp.read_bytes(), tmp.name)            # the platform must be able to read it
        if check.returns.shape[1] != len(config["assets"]) or check.frequency != "daily":
            raise RuntimeError(f"Check failed: read back {check.returns.shape[1]} assets at {check.frequency} frequency.")
        if out.exists():
            shutil.copy2(out, out.with_suffix(".prev.csv"))
        tmp.replace(out)
        (out.parent / "dc_plan_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        log(f"Done: {meta['rows']} days from {meta['start']} to {meta['end']} written to {out.relative_to(ROOT)}")
        for w in meta["warnings"]:
            log(f"WARNING {w}")
        try:                                   # between-quarter check of the last recorded rebalancing decision
            decisions.drift_check(out, log=log)
        except Exception as exc:
            log(f"Drift check failed: {exc}")
        return 0
    except Exception as exc:
        log(f"FAILED: {exc}")
        log(traceback.format_exc().strip().splitlines()[-1])
        log("The previous data file was left unchanged.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
