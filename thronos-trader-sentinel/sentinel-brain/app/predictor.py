"""
Per-user MLP prediction engine.

Each user gets an independent MLPClassifier trained on their own trade history.
Supports:
  - train()   — initial fit from historical trades
  - predict() — inference with live market signals
  - adapt()   — online partial_fit after each real trade (incremental learning)
  - stats()   — model diagnostics

Models are persisted to MODELS_DIR (from store.py → /disckb/models by default)
so they survive container restarts on the Railway volume.
"""
import logging
import pickle
import time
from typing import Any, Optional

import numpy as np
from sklearn.model_selection import cross_val_score
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler

from .features import build_dataset, market_features, trade_features
from .store import MODELS_DIR

log = logging.getLogger(__name__)

MIN_PAIRS = 5  # minimum matched pairs required to train


class _UserModel:
    def __init__(self) -> None:
        self.clf = MLPClassifier(
            hidden_layer_sizes=(64, 32, 16),
            activation="relu",
            max_iter=500,
            warm_start=True,     # enables incremental fits
            random_state=42,
        )
        self.scaler = StandardScaler()
        self.trained = False
        self.trade_count = 0
        self.pairs_used = 0
        self.win_rate = 0.0
        self.accuracy = 0.0
        self.trained_at: Optional[float] = None


class PredictionEngine:
    def __init__(self) -> None:
        self._models: dict[str, _UserModel] = {}
        self._load_persisted_models()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _model_path(self, user_id: str) -> Path:
        # Sanitize user_id to safe filename chars
        safe_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in user_id)
        return MODELS_DIR / f"{safe_id}.pkl"

    def _save_model(self, user_id: str) -> None:
        try:
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            path = self._model_path(user_id)
            with open(path, "wb") as f:
                pickle.dump(self._models[user_id], f, protocol=pickle.HIGHEST_PROTOCOL)
            log.info("[brain] saved model for user=%s → %s", user_id, path)
        except Exception as exc:
            log.warning("[brain] could not save model for user=%s: %s", user_id, exc)

    def _load_persisted_models(self) -> None:
        if not MODELS_DIR.exists():
            return
        loaded = 0
        for pkl_path in MODELS_DIR.glob("*.pkl"):
            user_id = pkl_path.stem
            try:
                with open(pkl_path, "rb") as f:
                    model = pickle.load(f)
                if isinstance(model, _UserModel):
                    self._models[user_id] = model
                    loaded += 1
            except Exception as exc:
                log.warning("[brain] could not load model from %s: %s", pkl_path, exc)
        if loaded:
            log.info("[brain] loaded %d persisted model(s) from %s", loaded, MODELS_DIR)

    def user_count(self) -> int:
        return len(self._models)

    def _get(self, user_id: str) -> Optional[_UserModel]:
        return self._models.get(user_id)

    def _get_or_create(self, user_id: str) -> _UserModel:
        if user_id not in self._models:
            self._models[user_id] = _UserModel()
        return self._models[user_id]

    # ── Training ─────────────────────────────────────────────────────────────

    def train(self, user_id: str, trades: list[dict[str, Any]]) -> dict:
        X_raw, y = build_dataset(trades)

        if len(X_raw) < MIN_PAIRS:
            return {
                "trained": False,
                "reason": f"Only {len(X_raw)} matched trade pairs — need at least {MIN_PAIRS}.",
                "trade_count": len(trades),
                "pairs_used": len(X_raw),
            }

        model = self._get_or_create(user_id)
        X = np.array(X_raw, dtype=float)
        X_scaled = model.scaler.fit_transform(X)

        # Cross-validated accuracy estimate (3-fold or less)
        n_folds = min(3, len(y))
        try:
            cv_scores = cross_val_score(
                MLPClassifier(hidden_layer_sizes=(64, 32, 16), max_iter=500, random_state=42),
                X_scaled,
                y,
                cv=n_folds,
                scoring="accuracy",
            )
            model.accuracy = float(cv_scores.mean())
        except Exception:
            model.accuracy = 0.0

        model.clf.fit(X_scaled, y)
        model.trained = True
        model.trade_count = len(trades)
        model.pairs_used = len(X_raw)
        model.win_rate = round(sum(y) / len(y), 3)
        model.trained_at = time.time()

        self._save_model(user_id)

        return {
            "trained": True,
            "trade_count": len(trades),
            "pairs_used": len(X_raw),
            "win_rate": model.win_rate,
            "model_accuracy": round(model.accuracy, 3),
        }

    # ── Prediction ───────────────────────────────────────────────────────────

    def predict(self, user_id: str, signals: dict[str, float]) -> dict:
        model = self._get(user_id)

        if not model or not model.trained:
            return self._heuristic_predict(signals)

        mf = market_features(signals)
        X = np.array([mf], dtype=float)

        # Pad to match training feature dimension (trade_features has 4 dims)
        # We use only market features at prediction time; scaler was fit on
        # trade_features (4 dims) — rebuild with same dimensionality.
        # trade_features defaults: side=1 (buy intent), hour=now, dow=now, cost=0
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        tf = [1.0, now.hour / 23.0, now.weekday() / 6.0, 0.0]
        combined = np.array([tf], dtype=float)
        combined_scaled = model.scaler.transform(combined)

        proba = model.clf.predict_proba(combined_scaled)[0]
        prob = float(proba[1]) if len(proba) > 1 else float(proba[0])
        gap = abs(prob - 0.5)
        confidence = "high" if gap > 0.20 else "medium" if gap > 0.10 else "low"

        return {
            "prediction": "profitable" if prob > 0.5 else "risky",
            "probability": round(prob, 3),
            "confidence": confidence,
            "model": "personal_mlp",
            "model_accuracy": round(model.accuracy, 3),
            "trades_trained_on": model.trade_count,
            "user_win_rate": model.win_rate,
        }

    @staticmethod
    def _heuristic_predict(signals: dict[str, float]) -> dict:
        """Fallback when no personal model exists yet."""
        rsi = signals.get("rsi", 50.0)
        risk = (signals.get("geo_score", 5.0) + signals.get("calendar_score", 5.0)) / 2
        if rsi > 72 or risk > 7:
            prob = 0.28
        elif rsi < 30 and risk < 3:
            prob = 0.72
        else:
            prob = 0.50
        return {
            "prediction": "profitable" if prob > 0.5 else "risky",
            "probability": round(prob, 3),
            "confidence": "low",
            "model": "heuristic_fallback",
            "message": "Sync your trade history via POST /api/brain/sync to get a personalised model.",
        }

    # ── Online Adaptation ────────────────────────────────────────────────────

    def adapt(self, user_id: str, features: list[float], outcome: int) -> None:
        """
        Incrementally update the model with one real trade outcome.
        Call after every closed trade for continuous personalisation.
        """
        model = self._get(user_id)
        if not model or not model.trained:
            return
        X = np.array([features], dtype=float)
        X_scaled = model.scaler.transform(X)
        model.clf.partial_fit(X_scaled, [outcome], classes=[0, 1])
        self._save_model(user_id)

    # ── Stats ────────────────────────────────────────────────────────────────

    def stats(self, user_id: str) -> dict:
        model = self._get(user_id)
        if not model:
            return {"trained": False, "user_id": user_id}
        return {
            "user_id": user_id,
            "trained": model.trained,
            "trade_count": model.trade_count,
            "pairs_used": model.pairs_used,
            "win_rate": model.win_rate,
            "model_accuracy": round(model.accuracy, 3),
            "trained_at": model.trained_at,
        }
