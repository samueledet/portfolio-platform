"""Data-feed transforms, tested offline with synthetic inputs."""
import numpy as np
import pandas as pd
import pytest

from pcp import feeds
from pcp.engine import data

CAL = pd.bdate_range("2024-01-01", "2024-03-29")


def test_accrue_matches_simple_interest_per_calendar_day():
    idx = feeds.accrue(pd.Series([3.6], index=[pd.Timestamp("2023-12-29")]), CAL)
    days = (CAL[-1] - CAL[0]).days
    assert idx.iloc[0] == 100 and idx.iloc[-1] / 100 == pytest.approx((1 + 0.036 / 360) ** days, rel=1e-3)
    fri, mon = idx[pd.Timestamp("2024-01-05")], idx[pd.Timestamp("2024-01-08")]
    assert mon / fri - 1 == pytest.approx(0.036 * 3 / 360)               # weekends earn interest too


def test_splice_chains_returns_at_the_join():
    early = pd.Series([50.0, 55.0, 60.5], index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]))
    main = pd.Series([200.0, 210.0], index=pd.to_datetime(["2024-01-03", "2024-01-04"]))
    s = feeds.splice(main, early)
    assert list(s.index.strftime("%m-%d")) == ["01-01", "01-02", "01-03", "01-04"]
    np.testing.assert_allclose(s.pct_change().dropna().values, [0.10, 0.10, 0.05])


def test_cpi_plus_compounds_inflation_and_real_return():
    cpi = pd.Series([100.0, 101.0, 102.01, 103.0301], index=pd.to_datetime(["2023-12-01", "2024-01-01", "2024-02-01", "2024-03-01"]))
    cal = pd.DatetimeIndex(["2023-12-31", "2024-03-31"])
    s, est = feeds.cpi_plus(cpi, cal, 0.0)
    assert s.iloc[-1] / 100 == pytest.approx(1.01 ** 3, rel=1e-9) and est == []
    s3, _ = feeds.cpi_plus(cpi, cal, 0.03)
    assert s3.iloc[-1] / s.iloc[-1] == pytest.approx(1.03 ** (91 / 365), rel=1e-9)
    _, est = feeds.cpi_plus(cpi, pd.DatetimeIndex(["2024-03-31", "2024-05-15"]), 0.0)
    assert est == ["2024-04", "2024-05"]                                 # unpublished months are reported


def test_missing_cpi_month_is_interpolated():
    cpi = pd.Series([100.0, 104.04], index=pd.to_datetime(["2024-01-01", "2024-03-01"]))
    filled, gaps = feeds.fill_months(cpi)
    assert gaps == ["2024-02"] and filled["2024-02-01"] == pytest.approx(102.0)


def test_written_file_loads_with_names_and_proxies(tmp_path):
    df = pd.DataFrame({"S&P 500": np.linspace(100, 110, 30), "US 3M TB": np.linspace(100, 100.3, 30)}, index=pd.bdate_range("2024-01-01", periods=30))
    meta = {"assets": [{"name": "S&P 500", "proxy": "SPDR S&P 500 ETF (SPY)"}, {"name": "US 3M TB", "proxy": "13-week bill, accrued"}]}
    path = tmp_path / "x.csv"
    feeds.write_csv(df, meta, path)
    ds = data.load_table(path.read_bytes(), "x.csv")
    assert ds.assets == ["S&P 500", "US 3M TB"] and ds.names[0] == "SPDR S&P 500 ETF (SPY)" and ds.frequency == "daily"
