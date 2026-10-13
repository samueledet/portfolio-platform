"""Where a portfolio's return and its active return against a benchmark came from, asset by asset."""
from __future__ import annotations

import numpy as np


def _carino(rp: np.ndarray, rb: np.ndarray) -> np.ndarray:
    """Per-period scaling factors that make arithmetic active effects add up to the compounded active return."""
    Rp, Rb = np.prod(1 + rp) - 1, np.prod(1 + rb) - 1
    k = (np.log1p(Rp) - np.log1p(Rb)) / (Rp - Rb) if abs(Rp - Rb) > 1e-12 else 1 / (1 + Rp)
    diff = rp - rb
    safe = np.where(np.abs(diff) > 1e-12, diff, 1.0)
    kt = np.where(np.abs(diff) > 1e-12, (np.log1p(rp) - np.log1p(rb)) / safe, 1 / (1 + rp))
    return kt / k


def attribute(returns: np.ndarray, weights, benchmark_weights=None) -> dict:
    """Return contribution per asset, and allocation effect against a benchmark held in the same assets.

    Both portfolios are rebalanced to their weights every period. Contributions add up exactly to the
    cumulative return; allocation effects (Brinson-Fachler, Carino-linked) add up exactly to the active return.
    With asset-class data both portfolios hold the same instruments, so there is no selection effect.
    """
    r = np.asarray(returns, dtype=float)
    w = np.asarray(weights, dtype=float)
    w = w / w.sum()
    rp = r @ w
    base = np.concatenate([[1.0], np.cumprod(1 + rp)[:-1]])          # wealth at the start of each period
    out = {
        "weights": w.tolist(),
        "asset_return": (np.prod(1 + r, axis=0) - 1).tolist(),
        "contribution": ((r * w) * base[:, None]).sum(axis=0).tolist(),
        "total_return": float(np.prod(1 + rp) - 1),
    }
    if benchmark_weights is not None:
        b = np.asarray(benchmark_weights, dtype=float)
        b = b / b.sum()
        rb = r @ b
        scale = _carino(rp, rb)
        effect = ((w - b)[None, :] * (r - rb[:, None]) * scale[:, None]).sum(axis=0)
        out.update({
            "benchmark_weights": b.tolist(), "active_weights": (w - b).tolist(),
            "allocation_effect": effect.tolist(),
            "benchmark_return": float(np.prod(1 + rb) - 1),
            "active_return": float(np.prod(1 + rp) - np.prod(1 + rb)),
        })
    return out
