"""Load a client file (CSV or Excel) of prices or returns into a clean returns table."""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

FREQUENCIES = {"daily": 252, "weekly": 52, "monthly": 12, "quarterly": 4, "annual": 1}


@dataclass
class Dataset:
    assets: list[str]
    names: list[str]                 # optional long names (asset classes); defaults to assets
    dates: pd.DatetimeIndex          # one date per return row
    returns: np.ndarray              # T x N simple returns
    kind: str                        # "prices" or "returns" as uploaded
    frequency: str
    periods_per_year: int
    prices: np.ndarray | None = None  # (T+1) x N when prices were uploaded
    price_dates: pd.DatetimeIndex | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def n_assets(self) -> int:
        return len(self.assets)


def _read_raw(content: bytes, filename: str, sheet: str | None) -> pd.DataFrame:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(io.BytesIO(content), sheet_name=sheet or 0, header=None)
    text = content.decode("utf-8-sig", errors="replace")
    sep = ";" if text.count(";") > text.count(",") else ("\t" if text.count("\t") > text.count(",") else ",")
    return pd.read_csv(io.StringIO(text), header=None, sep=sep)


def _find_date_column(raw: pd.DataFrame) -> int:
    best, best_share = 0, 0.0
    for c in range(min(raw.shape[1], 5)):
        col = raw.iloc[:, c]
        if pd.api.types.is_numeric_dtype(col.dropna()):
            continue
        share = pd.to_datetime(col, errors="coerce", format="mixed").notna().mean()
        if share > best_share:
            best, best_share = c, share
    if best_share < 0.5:
        raise ValueError("No date column found. The first column should hold dates (e.g. 2024-01-31).")
    return best


def detect_frequency(dates: pd.DatetimeIndex) -> str:
    gap = float(np.median(np.diff(dates.values).astype("timedelta64[D]").astype(float)))
    if gap <= 4:
        return "daily"
    if gap <= 9:
        return "weekly"
    if gap <= 35:
        return "monthly"
    if gap <= 100:
        return "quarterly"
    return "annual"


def detect_kind(values: np.ndarray) -> str:
    """Returns are small numbers around zero with negatives; prices are positive levels."""
    finite = values[np.isfinite(values)]
    if finite.size and (finite <= 0).mean() > 0.02 and np.median(np.abs(finite)) < 0.25:
        return "returns"
    return "prices"


def load_table(content: bytes, filename: str, *, kind: str = "auto", frequency: str = "auto",
               sheet: str | None = None) -> Dataset:
    raw = _read_raw(content, filename, sheet).dropna(how="all").dropna(axis=1, how="all").reset_index(drop=True)
    raw.columns = range(raw.shape[1])
    dcol = _find_date_column(raw)
    parsed = pd.to_datetime(raw.iloc[:, dcol], errors="coerce", format="mixed")
    first = int(parsed.notna().idxmax())            # first data row; everything above is header
    header = raw.iloc[:first].drop(columns=dcol)
    body = raw.iloc[first:].copy()
    body_dates = pd.to_datetime(body.iloc[:, dcol], errors="coerce", format="mixed")
    body = body[body_dates.notna()]
    dates = pd.DatetimeIndex(body_dates[body_dates.notna()]).normalize()
    values = body.drop(columns=dcol).apply(pd.to_numeric, errors="coerce")

    keep = [c for c in values.columns if values[c].notna().mean() > 0.5]
    dropped = [c for c in values.columns if c not in keep]
    values = values[keep]
    if values.shape[1] < 2:
        raise ValueError("At least two numeric asset columns are needed.")

    # header rows: the last one is the ticker / short name, the one above (if any) a long name
    def label(row: int, c, fallback: str) -> str:
        if len(header) < -row:
            return fallback
        text = str(header.iloc[row][c]).strip()
        return text if text and text.lower() != "nan" else fallback
    assets = [label(-1, c, f"Asset {i + 1}") for i, c in enumerate(keep)]
    names = [label(-2, c, assets[i]) for i, c in enumerate(keep)]

    warnings: list[str] = []
    if dropped:
        warnings.append(f"Ignored {len(dropped)} non-numeric column(s).")
    order = np.argsort(dates.values, kind="stable")
    dates, values = dates[order], values.iloc[order].reset_index(drop=True)
    dup = dates.duplicated()
    if dup.any():
        warnings.append(f"Removed {int(dup.sum())} duplicate date(s).")
        dates, values = dates[~dup], values[~dup].reset_index(drop=True)

    gaps = int(values.isna().sum().sum())
    if kind == "auto":
        kind = detect_kind(values.to_numpy(dtype=float))
    if kind == "prices":
        if gaps:
            warnings.append(f"Filled {gaps} missing price(s) with the previous value.")
            values = values.ffill()
        ok = values.notna().all(axis=1)
        if (~ok).any():
            warnings.append(f"Dropped {int((~ok).sum())} leading row(s) before all assets have prices.")
        values, dates = values[ok.values], dates[ok.values]
        prices = values.to_numpy(dtype=float)
        if (prices <= 0).any():
            raise ValueError("Prices must be positive. If the file holds returns, choose 'returns'.")
        returns = prices[1:] / prices[:-1] - 1
        ret_dates, price_dates = dates[1:], dates
    else:
        vals = values.to_numpy(dtype=float)
        if np.nanmedian(np.abs(vals)) > 0.5:       # returns given in percent (e.g. 1.2 for 1.2%)
            vals = vals / 100
            warnings.append("Returns looked like percentages and were divided by 100.")
        ok = np.isfinite(vals).all(axis=1)
        if (~ok).any():
            warnings.append(f"Dropped {int((~ok).sum())} row(s) with missing returns.")
        returns, ret_dates, prices, price_dates = vals[ok], dates[ok], None, None

    if returns.shape[0] < 12:
        raise ValueError(f"Only {returns.shape[0]} return observations after cleaning; at least 12 are needed.")
    if frequency == "auto":
        frequency = detect_frequency(ret_dates)
    return Dataset(assets=assets, names=names, dates=ret_dates, returns=returns, kind=kind, frequency=frequency,
                   periods_per_year=FREQUENCIES[frequency], prices=prices, price_dates=price_dates, warnings=warnings)


def slice_dates(ds: Dataset, start: str | None, end: str | None) -> np.ndarray:
    """Boolean mask over return rows for an inclusive [start, end] window."""
    mask = np.ones(len(ds.dates), dtype=bool)
    if start:
        mask &= ds.dates >= pd.Timestamp(start)
    if end:
        mask &= ds.dates <= pd.Timestamp(end)
    return mask
