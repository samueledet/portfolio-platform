# Portfolio Construction Platform (prototype)

A workspace where buy-side investors (sovereign wealth funds, endowments, asset managers) upload their own asset data and run a portfolio-construction study: statistics, optimisation under constraints, backtests, stress tests and Monte Carlo simulation.

This is a prototype. It runs on one machine, has no sign-in, and keeps uploaded data in memory until the server stops.

## Run it

```powershell
.\run.ps1          # opens http://127.0.0.1:8800
```

Or directly:

```bash
python -m uvicorn pcp.api:app --host 127.0.0.1 --port 8800
```

First-time setup, if the environment does not exist yet:

```bash
python -m venv --system-site-packages %USERPROFILE%\.venvs\portfolio-platform
%USERPROFILE%\.venvs\portfolio-platform\Scripts\python -m pip install -r requirements.txt
```

Demo links load a sample and run through to a step, for example
`http://127.0.0.1:8800/?demo=indices&step=backtest` (`demo` = `etfs` or `indices`; `step` = `data`, `assume`, `optimise`, `backtest`, `stress` or `simulate`).

## What a user can do

| Step | What it does |
|---|---|
| 1 Data | Upload CSV or Excel (dates in the first column, one column per asset). Prices or returns and the frequency are detected and can be overridden. Shows growth, statistics, correlation and return distributions. |
| 2 Assumptions | Use historical estimates or type custom expected returns and risk. Choose the annualisation method, sample or shrinkage covariance, and an optimistic or pessimistic tilt. Set minimum and maximum weights and group limits. |
| 3 Optimise | Efficient frontier under the constraints, with minimum variance, maximum Sharpe, maximum return, risk parity and equal weight as reference portfolios. Add portfolios for a risk limit or return target, or type custom weights. |
| 4 Backtest | Apply each portfolio to the actual returns with a chosen rebalancing rule and trading cost. Reports return, risk, Sharpe, Sortino, drawdown, VaR and CVaR, plus tracking error and information ratio against a benchmark. |
| 5 Stress test | Portfolio behaviour in the crisis periods the data covers and in its own worst drawdown, plus a severity slider for any window. |
| 6 Simulate | Monte Carlo projection (normal, fat-tailed or bootstrapped history): range of outcomes, probability of loss, VaR and CVaR, drawdowns. |
| Export Excel | An audit workbook with inputs, assumptions, portfolios and backtest results. |

## Layout

```
pcp/engine/       analytics library: pure functions, no web code
  data.py         file loading, type and frequency detection, cleaning
  stats.py        statistics, covariance, assumption blending and tilts
  optimize.py     constrained mean-variance optimisation (CVXPY), frontier, risk parity
  performance.py  rebalanced portfolio returns and performance metrics
  risk.py         stress windows, shock sensitivity, Monte Carlo
pcp/api.py        FastAPI endpoints over the engine; serves the web app
web/              interface (plain JavaScript, SVG charts, no build step)
tests/            reference tests and API tests
sample_data/      the two demo datasets
```

## Tests

```bash
python -m pytest tests -q
```

`tests/test_reference.py` checks the engine against the completed course workbooks (fixtures in `tests/fixtures`). With the course data loaded it reproduces:

- Unit 3: Solver's minimum-risk portfolio and the 14.80% return at 12% volatility
- Unit 4: the optimistic CMEs, the SAA weights, and the 3, 5 and 10-year backtest tables
- Unit 5: the SAA weights, the 1.44× oil-shock break-even, and the Monte Carlo scenario inputs
- Unit 6: return, volatility, downside deviation, Sortino, skewness and kurtosis for SAA and TAA

## Not built yet

- Sign-in, separate storage per client, and a database (data is in memory only)
- Custom correlation input and blending forecasts from several managers in the interface (the engine supports both)
- Black–Litterman, CVaR optimisation, performance attribution, currency hedging, spending-policy simulation
- Word or PDF investment-committee report

Analytics output is for review. It is not investment advice.
