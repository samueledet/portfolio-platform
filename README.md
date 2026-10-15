# Portfolio Construction Platform (prototype)

A workspace where buy-side investors (sovereign wealth funds, endowments, asset managers) upload their own asset data and run a portfolio-construction study, from statistics to an investment-committee report.

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
`http://127.0.0.1:8800/?demo=indices&step=spending`.
`demo` is `etfs`, `indices` or `fx`; `step` is `data`, `assume`, `optimise`, `backtest`, `stress`, `simulate`, `spending` or `currency`.

## What a user can do

| Step | What it does |
|---|---|
| 1 Data | Upload CSV or Excel (dates in the first column, one column per asset). Prices or returns and the frequency are detected and can be overridden. |
| 2 Assumptions | Expected returns and risk from history, custom forecasts, a weighted blend of several forecast sets, or Black–Litterman views. Custom correlations, sample or shrinkage covariance, optimistic and pessimistic tilts. Minimum, maximum and group weight limits. |
| 2 Mandate | Long-only fully invested, long-only with cash, long/short (e.g. 130/30) with net and gross exposure limits and short borrow costs, market neutral, leveraged with a borrowing spread, benchmark-relative with a tracking-error budget and active-weight limits, or surplus / liability-driven with a funding ratio. A turnover limit against the current portfolio works with any of them. Every later step (optimiser, backtest, stress test, simulation, spending, attribution, reports) follows the mandate. |
| 3 Optimise | Efficient frontier under the constraints, with reference portfolios. Add portfolios for a risk limit, a return target, maximum Sharpe, minimum variance, minimum tail loss (CVaR) or risk parity, or type custom weights. |
| 4 Backtest | Apply each portfolio to the actual returns with a rebalancing rule and trading cost. Attribution shows each asset's contribution to return and the allocation effect against a benchmark. |
| 5 Stress test | Portfolio behaviour in the crisis periods the data covers and in its own worst drawdown, plus a severity slider for any window. |
| 6 Simulate | Monte Carlo projection (normal, fat-tailed or bootstrapped history). |
| 7 Spending policy | Projects fund value and payouts in today's money under a spending rule (percentage of value, smoothed, hybrid, or fixed real amount), with inflation, contributions and costs. Reports the probability of preserving real value and compares spending rates. |
| 8 Currency | For a foreign-currency holding: local, unhedged and hedged returns at any hedge ratio, and the risk at each ratio. |
| Committee report | A Word document for one proposed portfolio: summary, assumptions, portfolios, backtest, attribution, stress tests, simulated outlook and spending policy, with charts. |
| Export Excel | An audit workbook with inputs, assumptions, portfolios and backtest results. |

## Layout

```
pcp/engine/       analytics library: pure functions, no web code
  data.py         file loading, type and frequency detection, cleaning
  stats.py        statistics, covariance, blending, Black-Litterman, correlation repair
  mandate.py      investment mandates: exposure limits, cash, leverage, shorts, benchmark and liability reference
  optimize.py     constrained optimisation (CVXPY) under any mandate: mean-variance, CVaR, risk parity, frontier
  performance.py  rebalanced portfolio returns and performance metrics
  attribution.py  return contribution and allocation effect
  risk.py         stress windows, shock sensitivity, Monte Carlo
  spending.py     spending-policy simulation
  currency.py     currency hedge analysis
pcp/report.py     committee report (Word, with matplotlib charts)
pcp/api.py        FastAPI endpoints over the engine; serves the web app
web/              interface (plain JavaScript, SVG charts, no build step)
tests/            reference tests, extension tests and API tests
sample_data/      the three demo datasets
```

## Tests

```bash
python -m pytest tests -q
```

`tests/test_reference.py` checks the engine against the completed course workbooks (fixtures in `tests/fixtures`). With the course data loaded it reproduces:

- Unit 3: Solver's minimum-risk portfolio and the 14.80% return at 12% volatility
- Unit 4: the optimistic CMEs, the SAA weights, and the 3, 5 and 10-year backtest tables
- Unit 5: the SAA weights, the 1.44× oil-shock break-even, and the Monte Carlo scenario inputs
- Unit 6: return, volatility, downside deviation, Sortino, skewness and kurtosis for SAA and TAA, and the unhedged return of the UK carve-out

`tests/test_mandates.py` checks each mandate: limits hold, a wider mandate never does worse, market-neutral cash earns the risk-free rate, a fully funded surplus mandate hedges the liability exactly, and the tracking-error budget binds.

`tests/test_extensions.py` covers the methods the workbooks do not have. Examples: a certain Black–Litterman view is honoured exactly, attribution effects add up to the active return, a zero-volatility spending case matches a hand calculation.

## Not built yet

- Sign-in, separate storage per client, and a database (data is in memory only)
- A React interface: Node.js is installed, but the npm registry is blocked on this network, so the interface stays in plain JavaScript for now
- Hosting

Analytics output is for review. It is not investment advice.
