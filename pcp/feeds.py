"""Daily total-return series for a list of asset classes, built from public sources.

Sources: Yahoo Finance (ETF prices with dividends reinvested, T-bill rate), the ECB (euro cash rates and the
EUR/USD reference rate) and the US Bureau of Labor Statistics (CPI). Each asset is described in a JSON config
(see config/dc_plan.json); build() returns one table of total-return indices (start = 100) on a common calendar.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import time
import urllib.request

import numpy as np
import pandas as pd

UA = {"User-Agent": "Mozilla/5.0 (portfolio-platform data feed)"}


# ------------------------------------------------------------------ downloads
def http_get(url: str, *, data: bytes | None = None, headers: dict | None = None, retries: int = 3, timeout: int = 40) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:                      # network hiccups: back off and retry
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Download failed after {retries} tries: {url.split('?')[0]} ({last})")


def yahoo(ticker: str, start: str, field: str = "adjclose") -> pd.Series:
    """Daily adjusted close (dividends and splits reinvested) or plain close, indexed by exchange date."""
    p1 = int(pd.Timestamp(start).timestamp())
    p2 = int(time.time()) + 86400
    sym = urllib.request.quote(ticker)
    raw = json.loads(http_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}"
                              "&interval=1d&events=div%2Csplit&includeAdjustedClose=true"))
    res = (raw.get("chart") or {}).get("result")
    if not res:
        raise RuntimeError(f"Yahoo returned no data for {ticker}: {(raw.get('chart') or {}).get('error')}")
    r = res[0]
    offset = r["meta"].get("gmtoffset", 0)
    dates = pd.to_datetime(np.array(r["timestamp"]) + offset, unit="s").normalize()
    values = r["indicators"]["adjclose"][0]["adjclose"] if field == "adjclose" else r["indicators"]["quote"][0]["close"]
    s = pd.Series(values, index=dates, dtype=float).dropna()
    return s[~s.index.duplicated(keep="last")].sort_index()


def ecb(key: str, start: str) -> pd.Series:
    """One ECB data-portal series, e.g. 'EST/B.EU000A2X2A25.WT' (euro short-term rate)."""
    txt = http_get(f"https://data-api.ecb.europa.eu/service/data/{key}?format=csvdata&startPeriod={start}").decode("utf-8")
    df = pd.read_csv(io.StringIO(txt), usecols=["TIME_PERIOD", "OBS_VALUE"])
    return pd.Series(df["OBS_VALUE"].astype(float).values, index=pd.to_datetime(df["TIME_PERIOD"])).dropna().sort_index()


def bls(series_id: str, start_year: int) -> pd.Series:
    """Monthly BLS series (public API, no key: up to 10 years per request), indexed by month start."""
    out = {}
    end_year = dt.date.today().year
    for y0 in range(start_year, end_year + 1, 10):
        body = json.dumps({"seriesid": [series_id], "startyear": str(y0), "endyear": str(min(y0 + 9, end_year))}).encode()
        raw = json.loads(http_get("https://api.bls.gov/publicAPI/v2/timeseries/data/", data=body,
                                  headers={"Content-Type": "application/json"}))
        if raw.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError(f"BLS request failed: {raw.get('message')}")
        for obs in raw["Results"]["series"][0]["data"]:
            if obs["period"].startswith("M") and obs["period"] != "M13":
                try:
                    out[pd.Timestamp(int(obs["year"]), int(obs["period"][1:]), 1)] = float(obs["value"])
                except ValueError:                      # "-" marks a month that was not collected
                    continue
    return pd.Series(out).sort_index()


def fill_months(monthly: pd.Series) -> tuple[pd.Series, list[str]]:
    """Fill missing months inside a monthly series by log-linear interpolation; return the months filled."""
    full = monthly.reindex(pd.date_range(monthly.index[0], monthly.index[-1], freq="MS"))
    missing = [d.strftime("%Y-%m") for d in full.index[full.isna()]]
    return np.exp(np.log(full).interpolate()), missing


# ------------------------------------------------------------------ transforms (pure, testable)
def splice(main: pd.Series, earlier: pd.Series) -> pd.Series:
    """Extend `main` back in time with the returns of `earlier`, joined at main's first date."""
    join = main.index[0]
    before = earlier[earlier.index <= join]
    if before.empty:
        return main
    scaled = before * (main.iloc[0] / before.iloc[-1])
    return pd.concat([scaled[scaled.index < join], main]).sort_index()


def accrue(rate_pct: pd.Series, calendar: pd.DatetimeIndex, basis: int = 360) -> pd.Series:
    """Cash index from an annual rate in percent: each day earns the previous rate x calendar days / basis."""
    rate = rate_pct.reindex(rate_pct.index.union(calendar)).sort_index().ffill().reindex(calendar).bfill() / 100
    days = np.diff(calendar.values).astype("timedelta64[D]").astype(float)
    growth = np.concatenate([[1.0], 1 + rate.values[:-1] * days / basis])
    return pd.Series(100 * np.cumprod(growth), index=calendar)


def cpi_plus(cpi: pd.Series, calendar: pd.DatetimeIndex, real: float) -> tuple[pd.Series, list[str]]:
    """Daily index growing with monthly inflation (spread evenly over each month) plus a real return.

    Months without a published CPI repeat the latest monthly inflation rate; they are reported back.
    """
    cpi = cpi.sort_index()
    infl = (cpi / cpi.shift(1)).dropna()                     # month m inflation, indexed by month start
    last = infl.index[-1]
    estimated = []
    months = pd.period_range(calendar[0].to_period("M"), calendar[-1].to_period("M"), freq="M")
    daily_growth = {}
    for m in months:
        ts = m.to_timestamp()
        g = infl.get(ts)
        if g is None:
            g = infl.iloc[-1] if ts > last else infl.iloc[0]
            if ts > last:
                estimated.append(str(m))
        daily_growth[m] = g ** (1 / m.days_in_month) * (1 + real) ** (1 / 365)
    days = np.diff(calendar.values).astype("timedelta64[D]").astype(int)
    factors = [1.0]
    for i, n in enumerate(days):
        d0, f = calendar[i], 1.0
        for k in range(1, n + 1):                             # compound each calendar day in its own month
            f *= daily_growth[(d0 + pd.Timedelta(days=k)).to_period("M")]
        factors.append(f)
    return pd.Series(100 * np.cumprod(factors), index=calendar), estimated


def to_calendar(s: pd.Series, calendar: pd.DatetimeIndex, max_gap_days: int = 7) -> pd.Series:
    """Align a price series to the calendar, carrying values over short gaps such as foreign holidays."""
    joined = s.reindex(s.index.union(calendar)).sort_index()
    filled = joined.ffill(limit=max_gap_days).reindex(calendar)
    return filled


# ------------------------------------------------------------------ build
def build(config: dict, log=print) -> tuple[pd.DataFrame, dict]:
    """Download every asset in the config and return (total-return indices, metadata)."""
    start = config.get("start", "2008-01-02")
    cal_series = yahoo(config.get("calendar", "SPY"), start, "close")
    today = pd.Timestamp(dt.date.today())
    calendar = cal_series.index[(cal_series.index >= pd.Timestamp(start)) & (cal_series.index < today)]   # completed sessions only
    cols, meta_assets, warnings = {}, [], []
    fx_cache: dict = {}
    for a in config["assets"]:
        name, src = a["name"], a["source"]
        log(f"  {name}: {src} {a.get('ticker', a.get('series', ''))}")
        extra = {}
        if src == "yahoo":
            s = yahoo(a["ticker"], start)
            if a.get("splice"):
                s = splice(s, yahoo(a["splice"], start))
                extra["spliced_with"] = a["splice"]
            s = to_calendar(s, calendar)
        elif src == "cash_yield":
            s = accrue(yahoo(a["ticker"], start, "close"), calendar, a.get("basis", 360))
        elif src == "eur_cash":
            eonia = ecb("EON/D.EONIA_TO.RATE", start)
            estr = ecb("EST/B.EU000A2X2A25.WT", "2019-10-01")
            rate = pd.concat([eonia[eonia.index < estr.index[0]], estr]).sort_index()
            s = accrue(rate, calendar, 360)
            if a.get("currency", "USD") == "USD":
                if "fx" not in fx_cache:
                    fx_cache["fx"] = to_calendar(ecb("EXR/D.USD.EUR.SP00.A", start), calendar).bfill()
                fx = fx_cache["fx"]
                s = s * fx / fx.iloc[0]
        elif src == "cpi_plus":
            cpi, gaps = fill_months(bls(a["series"], calendar[0].year - 1))
            s, est = cpi_plus(cpi, calendar, float(a.get("real_return", 0.03)))
            if est:
                extra["estimated_months"] = est
            if gaps:
                extra["interpolated_months"] = gaps
                warnings.append(f"{name}: CPI was not published for {', '.join(gaps)}; those months were interpolated.")
        else:
            raise ValueError(f"Unknown source '{src}' for {name}.")
        first = s.first_valid_index()
        stale = int(s.isna()[::-1].cumprod().sum())             # trailing calendar days without a new value
        if first is None:
            raise RuntimeError(f"No data for {name}.")
        if first > calendar[0] + pd.Timedelta(days=10):
            warnings.append(f"{name} starts on {first.date()}, after the requested start; earlier rows are blank.")
        if stale > 5:
            warnings.append(f"{name} has no new value for the last {stale} trading days.")
        cols[name] = s.ffill()
        meta_assets.append({"name": name, "source": src, "proxy": a.get("proxy", ""), "note": a.get("note", ""),
                            "first_date": str(first.date()), "last_value_date": str(s.last_valid_index().date()),
                            "stale_trading_days": stale, **extra})
    df = pd.DataFrame(cols)
    df = df[df.notna().all(axis=1)]                              # common history only
    df = 100 * df / df.iloc[0]
    rets = df.pct_change().iloc[1:]
    for name in df.columns:
        big = rets[name].abs() > 0.25
        if big.any():
            warnings.append(f"{name} has {int(big.sum())} daily move(s) above 25%; check the source.")
    meta = {"name": config.get("name", ""), "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "start": str(df.index[0].date()), "end": str(df.index[-1].date()), "rows": int(len(df)),
            "base_currency": config.get("base_currency", "USD"), "assets": meta_assets, "warnings": warnings}
    return df, meta


def write_csv(df: pd.DataFrame, meta: dict, path) -> None:
    """Two header rows (proxy description, then asset name) so the platform shows both."""
    proxies = {a["name"]: a["proxy"] for a in meta["assets"]}
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(",".join(['"Proxy"'] + [f'"{proxies.get(c, "")}"' for c in df.columns]) + "\n")
        f.write(",".join(['"Date"'] + [f'"{c}"' for c in df.columns]) + "\n")
        for d, row in zip(df.index, df.values):
            f.write(d.strftime("%Y-%m-%d") + "," + ",".join(f"{v:.6f}" for v in row) + "\n")
