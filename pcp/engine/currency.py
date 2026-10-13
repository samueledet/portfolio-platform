"""Currency risk of a foreign-currency holding and the effect of hedging it with forwards."""
from __future__ import annotations

import numpy as np

from .performance import metrics, wealth_and_drawdown


def hedge_analysis(local_returns, fx_returns, *, ppy: int, hedge_ratio: float = 0.5,
                   base_rate: float = 0.0, foreign_rate: float = 0.0) -> dict:
    """Compare unhedged and hedged base-currency returns of an asset priced in a foreign currency.

    local_returns: asset returns in its own currency. fx_returns: change in the value of the foreign
    currency measured in the investor's base currency. The hedge sells the foreign currency forward on
    `hedge_ratio` of the position each period; by covered interest parity it earns the interest-rate
    differential (base minus foreign) less the currency move.
    """
    rl, fx = np.asarray(local_returns, dtype=float), np.asarray(fx_returns, dtype=float)
    if rl.shape != fx.shape or rl.ndim != 1:
        raise ValueError("The asset and currency series must have the same length.")
    carry = (1 + base_rate) ** (1 / ppy) / (1 + foreign_rate) ** (1 / ppy) - 1
    unhedged = (1 + rl) * (1 + fx) - 1
    hedge_leg = carry - fx                                   # return per unit hedged

    def stats(h: float) -> dict:
        s = unhedged + h * hedge_leg
        return {"hedge_ratio": h, "cumulative": float(np.prod(1 + s) - 1), "volatility": float(s.std(ddof=1) * np.sqrt(ppy))}

    var_fx = fx.var(ddof=1)
    min_var = float(np.cov(unhedged, fx, ddof=1)[0, 1] / var_fx) if var_fx > 0 else 0.0
    hedged = unhedged + hedge_ratio * hedge_leg
    series = {"Local currency": rl, "Unhedged": unhedged, "Hedged": hedged, "Currency": fx}
    return {
        "hedge_ratio": hedge_ratio, "carry_per_year": float((1 + carry) ** ppy - 1),
        "min_variance_hedge_ratio": min_var,
        "correlation_asset_currency": float(np.corrcoef(rl, fx)[0, 1]),
        "metrics": {k: metrics(v, ppy=ppy) for k, v in series.items()},
        "wealth": {k: wealth_and_drawdown(v)[0].tolist() for k, v in series.items()},
        "curve": [stats(h) for h in np.round(np.arange(0, 1.2001, 0.1), 2)],
        "hedge_value_added": float(np.prod(1 + hedged) - np.prod(1 + unhedged)),
    }
