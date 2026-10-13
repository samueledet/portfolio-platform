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


def resolve_assumptions(returns: np.ndarray, ppy: int, spec: dict | None) -> dict:
    """Turn a request's assumption settings into mu, vol, corr, cov.

    spec keys: source ("historical" | "custom"), method ("arithmetic" | "compound"), covariance
    ("sample" | "shrinkage"), mu / vol / corr (custom values; any left out fall back to historical),
    return_multiplier, vol_multiplier (scenario tilts, e.g. 1.10 and 0.90 for an optimistic view).
    """
    spec = spec or {}
    hist_cov = covariance(returns, ppy)
    shrinkage = None
    if spec.get("covariance") == "shrinkage":
        hist_cov, shrinkage = shrink_covariance(returns, ppy)
    hist_vol = np.sqrt(np.diag(hist_cov))
    hist_corr = hist_cov / np.outer(hist_vol, hist_vol)
    hist_mu = annualise_mean(returns.mean(axis=0), ppy, spec.get("method", "arithmetic"))

    custom = spec.get("source") == "custom"
    n = returns.shape[1]

    def pick(key, fallback):
        v = spec.get(key) if custom else None
        if v is None:
            return fallback
        arr = np.asarray(v, dtype=float)
        if arr.shape != fallback.shape:
            raise ValueError(f"'{key}' must have shape {fallback.shape} for {n} assets.")
        return arr
    mu, vol, corr = pick("mu", hist_mu), pick("vol", hist_vol), pick("corr", hist_corr)
    mu = mu * float(spec.get("return_multiplier", 1.0))
    vol = vol * float(spec.get("vol_multiplier", 1.0))
    if (vol <= 0).any():
        raise ValueError("Volatilities must be positive.")
    return {"mu": mu, "vol": vol, "corr": corr, "cov": cov_from_vol_corr(vol, corr), "shrinkage": shrinkage}
