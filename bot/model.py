"""A tiny, dependency-free learned model for the blind bot: two L2-regularised logistic regressions (buy-worthy / sell-worthy).

Deliberately simple. The point of the lab is the *procedure* - watch a forward-fed teacher, learn from its decisions, then
trade blind on data the model has never seen - not a clever model. A linear model on the starter features is easy to inspect
(`explain()` lists the weights) and cannot hide an overfit behind its complexity.

Models are plain JSON files in `data/models/` (override with LAB_MODELS), so a trained bot is just a name.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def models_dir() -> Path:
    return Path(os.environ.get("LAB_MODELS") or ROOT / "data" / "models")


def _safe(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", name or ""):
        raise ValueError("model names use letters, digits, '_', '-' and '.' only (max 60)")
    return name


def list_models() -> List[dict]:
    d = models_dir()
    out = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            m = json.loads(p.read_text())
            out.append({"name": p.stem, **m.get("meta", {})})
        except ValueError:
            continue
    return out


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 30) -> Tuple[np.ndarray, float]:
    """Newton-Raphson (IRLS) with a ridge penalty on the weights (not the intercept). X must be standardised."""
    n, k = X.shape
    A = np.hstack([X, np.ones((n, 1))])
    w = np.zeros(k + 1)
    pen = np.diag([l2] * k + [0.0])
    for _ in range(iters):
        p = sigmoid(A @ w)
        g = A.T @ (p - y) + pen @ w
        H = A.T @ (A * (p * (1 - p))[:, None]) + pen + 1e-9 * np.eye(k + 1)
        step = np.linalg.solve(H, g)
        w -= step
        if np.max(np.abs(step)) < 1e-7:
            break
    return w[:k], float(w[k])


@dataclass
class LinearModel:
    features: List[str]
    mean: List[float]
    std: List[float]
    w_buy: List[float]
    b_buy: float
    w_sell: List[float]
    b_sell: float
    meta: dict

    def vector(self, feats: Dict[str, float]) -> np.ndarray:
        x = np.array([feats.get(f, m) for f, m in zip(self.features, self.mean)], dtype=float)   # a missing feature = its mean
        return (x - np.array(self.mean)) / np.array(self.std)

    def predict(self, feats: Dict[str, float]) -> Tuple[float, float]:
        """(P(buy-worthy), P(sell-worthy)) for one decision."""
        x = self.vector(feats)
        return (float(sigmoid(x @ np.array(self.w_buy) + self.b_buy)), float(sigmoid(x @ np.array(self.w_sell) + self.b_sell)))

    def explain(self) -> List[dict]:
        rows = [{"feature": f, "w_buy": wb, "w_sell": ws} for f, wb, ws in zip(self.features, self.w_buy, self.w_sell)]
        return sorted(rows, key=lambda r: -max(abs(r["w_buy"]), abs(r["w_sell"])))

    def to_json(self) -> str:
        return json.dumps(self.__dict__)

    def save(self, name: str) -> Path:
        d = models_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{_safe(name)}.json"
        p.write_text(json.dumps({**self.__dict__}))
        return p

    @staticmethod
    def load(name: str) -> "LinearModel":
        p = models_dir() / f"{_safe(name)}.json"
        if not p.exists():
            raise ValueError(f"no trained model named {name!r} (train one in the back office, or `python -m bot.train`)")
        return LinearModel(**json.loads(p.read_text()))


def train_linear(X: np.ndarray, y_buy: np.ndarray, y_sell: np.ndarray, features: Sequence[str], l2: float, meta: dict) -> LinearModel:
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std < 1e-9] = 1.0
    Z = (X - mean) / std
    wb, bb = fit_logistic(Z, y_buy, l2)
    ws, bs = fit_logistic(Z, y_sell, l2)
    return LinearModel(list(features), mean.tolist(), std.tolist(), wb.tolist(), bb, ws.tolist(), bs, meta)
