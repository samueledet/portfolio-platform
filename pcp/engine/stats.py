"""Descriptive statistics and capital-market assumptions (expected return, risk, correlation)."""
from __future__ import annotations

import numpy as np


def annualise_mean(mean: np.ndarray, ppy: int, method: str = "arithmetic") -> np.ndarray:
    """arithmetic: mean x periods (optimiser convention). compound: (1 + mean)^periods - 1."""
    return (1 + mean) ** ppy - 1 if method == "compound" else mean * ppy


def skewness(x: np.ndarray) -> float:            # Excel SKEW
    n, s = len(x), x.std(ddof=1)
    return float(n / ((n - 1) * (n - 2)) * (((x - x.mean()) / s) ** 3).sum()) if n > 2 and s > 0 else float("nan")


def excess_kurtosis(x: np.ndarray) -> float:     # Excel KURT
    n, s = len(x), x.std(ddof=1)
    if n < 4 or s == 0:
        return float("nan")
    z4 = (((x - x.mean()) / s) ** 4).sum()
    return float(n * (n + 1) / ((n - 1) * (n - 2) * (n - 3)) * z4 - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3)))


def max_drawdown(returns: np.ndarray) -> float:
    wealth = np.concatenate([[1.0], np.cumprod(1 + returns)])
    return float((wealth / np.maximum.accumulate(wealth) - 1).min())


def asset_statistics(returns: np.ndarray, ppy: int, rf: float = 0.0, method: str = "arithmetic") -> list[dict]:
    out = []
    for i in range(returns.shape[1]):
        r = returns[:, i]
        m, sd = r.mean(), r.std(ddof=1)
        er, vol = float(annualise_mean(m, ppy, method)), float(sd * np.sqrt(ppy))
        out.append({
            "n": int(len(r)), "mean": float(m), "sd": float(sd), "exp_return": er, "volatility": vol,
            "sharpe": (er - rf) / vol if vol > 0 else None,
            "min": float(r.min()), "max": float(r.max()), "median": float(np.median(r)),
            "skew": skewness(r), "kurtosis": excess_kurtosis(r),
            "cumulative": float(np.prod(1 + r) - 1), "max_drawdown": max_drawdown(r),
        })
    return out


def covariance(returns: np.ndarray, ppy: int) -> np.ndarray:
    return np.cov(returns, rowvar=False, ddof=1) * ppy


def correlation(returns: np.ndarray) -> np.ndarray:
    return np.corrcoef(returns, rowvar=False)


def cov_from_vol_corr(vol: np.ndarray, corr: np.ndarray) -> np.ndarray:
    return np.outer(vol, vol) * corr


def shrink_covariance(returns: np.ndarray, ppy: int) -> tuple[np.ndarray, float]:
    """Ledoit-Wolf shrinkage towards a scaled identity; steadier than the sample matrix on short histories."""
    from sklearn.covariance import LedoitWolf
    lw = LedoitWolf().fit(returns)
    return lw.covariance_ * ppy, float(lw.shrinkage_)


def histogram(x: np.ndarray, bins: int = 30) -> dict:
    lo, hi = np.percentile(x, [0.5, 99.5])
    edges = np.linspace(lo, hi, bins + 1)
    counts, _ = np.histogram(np.clip(x, lo, hi), bins=edges)
    return {"edges": edges.tolist(), "counts": counts.tolist()}


def growth(returns: np.ndarray, base: float = 100.0) -> np.ndarray:
    """Value of `base` invested at the start, one row per date including the start."""
    return base * np.vstack([np.ones(returns.shape[1]), np.cumprod(1 + returns, axis=0)])


def blend_assumptions(sources: list[dict], weights: list[float] | None = None) -> dict:
    """Average several forecast sets ({mu, vol, corr}); weights default to equal."""
    w = np.array(weights if weights else [1.0] * len(sources), dtype=float)
    w = w / w.sum()
    out = {k: sum(wi * np.asarray(s[k], dtype=float) for wi, s in zip(w, sources)) for k in ("mu", "vol", "corr")}
    np.fill_diagonal(out["corr"], 1.0)
    return out


def repair_correlation(corr: np.ndarray) -> tuple[np.ndarray, str | None]:
    """Validate a user-supplied correlation matrix; if it is not positive semi-definite, return the nearest valid one."""
    c = np.asarray(corr, dtype=float)
    if c.ndim != 2 or c.shape[0] != c.shape[1]:
        raise ValueError("The correlation matrix must be square.")
    if np.abs(c - c.T).max() > 1e-6:
        raise ValueError("The correlation matrix must be symmetric.")
    if (np.abs(c) > 1 + 1e-9).any():
        raise ValueError("Correlations must lie between -1 and 1.")
    c = (c + c.T) / 2
    np.fill_diagonal(c, 1.0)
    vals, vecs = np.linalg.eigh(c)
    if vals.min() >= -1e-10:
        return c, None
    fixed = (vecs * np.clip(vals, 1e-8, None)) @ vecs.T
    d = np.sqrt(np.diag(fixed))
    fixed = fixed / np.outer(d, d)
    np.fill_diagonal(fixed, 1.0)
    return fixed, ("The correlations entered are not mutually consistent, so the nearest valid matrix was used "
                   f"(largest change {np.abs(fixed - c).max():.2f}).")


def black_litterman(cov: np.ndarray, market_weights, views: list[dict], *, rf: float = 0.0,
                    risk_aversion: float = 2.5, tau: float = 0.05) -> dict:
    """Blend equilibrium returns implied by market weights with investor views.

    views: {"type": "absolute", "asset": i, "value": r, "confidence": c} says asset i returns r a year;
           {"type": "relative", "long": i, "short": j, "value": r, "confidence": c} says i beats j by r.
    confidence in (0, 1]: 1 means the view is held with certainty, 0.5 weighs it equally with the prior.
    """
    cov = np.asarray(cov, dtype=float)
    n = cov.shape[0]
    w = np.asarray(market_weights, dtype=float)
    if w.shape != (n,) or w.sum() <= 0:
        raise ValueError(f"Black-Litterman needs {n} positive market weights.")
    w = w / w.sum()
    pi = risk_aversion * cov @ w                                   # implied excess returns
    if not views:
        return {"prior": pi + rf, "posterior": pi + rf, "views": 0}
    P, Q, conf = np.zeros((len(views), n)), np.zeros(len(views)), np.zeros(len(views))
    for k, v in enumerate(views):
        c = float(v.get("confidence", 0.5))
        if not 0 < c <= 1:
            raise ValueError("View confidence must be above 0% and at most 100%.")
        conf[k] = c
        if v.get("type") == "relative":
            if v["long"] == v["short"]:
                raise ValueError("A relative view needs two different assets.")
            P[k, int(v["long"])], P[k, int(v["short"])] = 1.0, -1.0
            Q[k] = float(v["value"])
        else:
            P[k, int(v["asset"])] = 1.0
            Q[k] = float(v["value"]) - rf                          # views are stated as total returns
    ts = tau * cov
    omega = np.diag(np.maximum((1 - conf) / conf, 1e-10) * np.einsum("ki,ij,kj->k", P, ts, P))
    post = pi + ts @ P.T @ np.linalg.solve(P @ ts @ P.T + omega, Q - P @ pi)
    return {"prior": pi + rf, "posterior": post + rf, "views": len(views)}


def resolve_assumptions(returns: np.ndarray, ppy: int, spec: dict | None, rf: float = 0.0) -> dict:
    """Turn a request's assumption settings into mu, vol, corr, cov.

    spec keys:
      source   "historical" | "custom" | "blend" | "black_litterman"
      method   "arithmetic" | "compound" (annualisation of historical means)
      covariance "sample" | "shrinkage"
      mu / vol / corr   custom values (any left out fall back to historical)
      sources  for "blend": [{"name", "weight", "mu", "vol", "corr"?}, ...]
      market_weights, views, risk_aversion, tau   for "black_litterman"
      return_multiplier, vol_multiplier   scenario tilts (e.g. 1.10 and 0.90 for an optimistic view)
    """
    spec = spec or {}
    notes: list[str] = []
    hist_cov = covariance(returns, ppy)
    shrinkage = None
    if spec.get("covariance") == "shrinkage":
        hist_cov, shrinkage = shrink_covariance(returns, ppy)
    hist_vol = np.sqrt(np.diag(hist_cov))
    hist_corr = hist_cov / np.outer(hist_vol, hist_vol)
    hist_mu = annualise_mean(returns.mean(axis=0), ppy, spec.get("method", "arithmetic"))
    n = returns.shape[1]
    source = spec.get("source", "historical")

    def shaped(value, fallback, key):
        if value is None:
            return fallback
        arr = np.asarray(value, dtype=float)
        if arr.shape != fallback.shape or not np.isfinite(arr).all():
            raise ValueError(f"'{key}' must hold {' x '.join(map(str, fallback.shape))} numbers for {n} assets.")
        return arr

    mu, vol, corr, extra = hist_mu, hist_vol, hist_corr, {}
    if source == "blend":
        sources = spec.get("sources") or []
        if not sources:
            raise ValueError("Add at least one forecast set to blend.")
        weights = [float(s.get("weight", 1.0)) for s in sources]
        if min(weights) < 0 or sum(weights) <= 0:
            raise ValueError("Forecast-set weights must be positive.")
        b = blend_assumptions([{"mu": shaped(s.get("mu"), hist_mu, "mu"), "vol": shaped(s.get("vol"), hist_vol, "vol"),
                                "corr": shaped(s.get("corr"), hist_corr, "corr")} for s in sources], weights)
        mu, vol, corr = b["mu"], b["vol"], b["corr"]
    elif source in ("custom", "black_litterman"):
        mu, vol = shaped(spec.get("mu"), hist_mu, "mu"), shaped(spec.get("vol"), hist_vol, "vol")
        corr = shaped(spec.get("corr"), hist_corr, "corr")
    elif source != "historical":
        raise ValueError("Assumption source must be historical, custom, blend or black_litterman.")
    if source != "historical":
        corr, note = repair_correlation(corr)
        if note:
            notes.append(note)
    if (vol <= 0).any():
        raise ValueError("Volatilities must be positive.")
    if source == "black_litterman":
        mkt = spec.get("market_weights")
        if mkt is None:
            mkt = np.full(n, 1 / n)
            notes.append("No market weights given, so equal weights were used as the equilibrium portfolio.")
        bl = black_litterman(cov_from_vol_corr(vol, corr), mkt, spec.get("views") or [], rf=rf,
                             risk_aversion=float(spec.get("risk_aversion", 2.5)), tau=float(spec.get("tau", 0.05)))
        mu, extra = bl["posterior"], {"prior_mu": bl["prior"]}
    mu = mu * float(spec.get("return_multiplier", 1.0))
    vol = vol * float(spec.get("vol_multiplier", 1.0))
    return {"mu": mu, "vol": vol, "corr": corr, "cov": cov_from_vol_corr(vol, corr), "shrinkage": shrinkage,
            "notes": notes, **extra}
