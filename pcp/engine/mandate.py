"""Investment mandates: how much may be invested, shorted or borrowed, and what risk is measured against.

Every weight vector w is read the same way under every mandate:
  sum(w)      net exposure; 1 - sum(w) is cash (negative cash = borrowing)
  sum(|w|)    gross exposure
  w < 0       short positions
A relative mandate measures risk and return against a reference portfolio d: a benchmark, or a liability
(d = liability asset / funding ratio, so w - d is the surplus position).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

KINDS = {
    "long_only": "Long-only, fully invested",
    "long_cash": "Long-only, cash allowed",
    "long_short": "Long/short with exposure limits",
    "market_neutral": "Market neutral",
    "leveraged": "Leveraged long-only",
    "benchmark_relative": "Benchmark-relative (tracking-error budget)",
    "surplus": "Surplus / liability-driven",
}


@dataclass
class Mandate:
    kind: str = "long_only"
    net_min: float = 1.0
    net_max: float = 1.0
    gross_max: float | None = None
    shorts: bool = False
    short_cost: float = 0.0          # annual fee on the short notional
    borrow_spread: float = 0.0       # annual spread over the risk-free rate on borrowed cash
    reference: np.ndarray | None = None
    relative: str | None = None      # "benchmark" | "surplus"
    active_limit: float | None = None
    risk_budget: float | None = None   # cap on tracking error / surplus risk (relative mandates)
    current: np.ndarray | None = None
    turnover_max: float | None = None
    liability: int | None = None
    funding_ratio: float = 1.0

    @property
    def label(self) -> str:
        return KINDS[self.kind]

    @property
    def full_investment(self) -> bool:
        return self.net_min == 1 and self.net_max == 1

    @property
    def simple(self) -> bool:
        """Plain long-only, fully invested, nothing relative: closed-form shortcuts apply."""
        return self.kind == "long_only" and self.turnover_max is None and self.reference is None

    def cash_return(self, weights, rf: float) -> float:
        """Annual return on the cash line (1 - net), borrowing at rf + spread when negative."""
        cash = 1 - float(np.sum(weights))
        return cash * (rf if cash >= 0 else rf + self.borrow_spread)

    def short_drag(self, weights) -> float:
        return self.short_cost * float(np.maximum(-np.asarray(weights, dtype=float), 0).sum())

    def expected_return(self, weights, mu, rf: float) -> float:
        w = np.asarray(weights, dtype=float)
        return float(w @ mu) + self.cash_return(w, rf) - self.short_drag(w)


def parse(spec: dict | None, n: int) -> Mandate:
    """Build a Mandate from request settings. Missing settings take each kind's usual defaults."""
    s = dict(spec or {})
    kind = s.get("kind", "long_only")
    if kind not in KINDS:
        raise ValueError(f"Mandate must be one of: {', '.join(KINDS)}.")
    f = lambda key, default: float(s[key]) if s.get(key) is not None else default
    m = Mandate(kind=kind, short_cost=f("short_cost", 0.0), borrow_spread=f("borrow_spread", 0.0))
    if kind == "long_cash":
        m.net_min, m.net_max = f("invested_min", 0.0), f("invested_max", 1.0)
    elif kind == "long_short":
        m.net_min = m.net_max = f("net", 1.0)
        m.gross_max, m.shorts = f("gross", 1.6), True
    elif kind == "market_neutral":
        m.net_min = m.net_max = 0.0
        m.gross_max, m.shorts = f("gross", 2.0), True
    elif kind == "leveraged":
        m.net_min, m.net_max = f("invested_min", 1.0), f("leverage", 1.5)
    elif kind == "benchmark_relative":
        b = np.asarray(s.get("benchmark") if s.get("benchmark") is not None else np.full(n, 1 / n), dtype=float)
        if b.shape != (n,) or b.sum() <= 0 or (b < 0).any():
            raise ValueError(f"The benchmark needs {n} non-negative weights.")
        m.reference, m.relative = b / b.sum(), "benchmark"
        m.active_limit = f("active_limit", None) if s.get("active_limit") is not None else None
    elif kind == "surplus":
        if s.get("liability") is None:
            raise ValueError("Choose the column that represents the liabilities.")
        L, F = int(s["liability"]), f("funding_ratio", 1.0)
        if not 0 <= L < n:
            raise ValueError("The liability column is not in the data.")
        if F <= 0:
            raise ValueError("The funding ratio must be positive.")
        m.liability, m.funding_ratio, m.relative = L, F, "surplus"
        m.reference = np.eye(n)[L] / F
    if m.relative and s.get("risk_budget") is not None:
        m.risk_budget = float(s["risk_budget"])
        if m.risk_budget <= 0:
            raise ValueError("The risk budget must be positive.")
    for key in ("net_min", "net_max", "gross_max"):                 # explicit overrides (custom mandates)
        if s.get(key) is not None:
            setattr(m, key, float(s[key]))
    if s.get("allow_shorts") is not None:
        m.shorts = bool(s["allow_shorts"])
    if m.net_min > m.net_max + 1e-12:
        raise ValueError("The minimum net exposure is above the maximum.")
    if m.gross_max is not None and m.gross_max < abs(m.net_min) - 1e-12:
        raise ValueError("Gross exposure cannot be below net exposure.")
    if s.get("turnover_max") is not None and s.get("current") is not None:
        cur = np.asarray(s["current"], dtype=float)
        if cur.shape != (n,):
            raise ValueError(f"The current portfolio needs {n} weights.")
        m.current, m.turnover_max = cur, float(s["turnover_max"])
    return m
