
import pickle
import numpy as np
import pandas as pd
from xgboost import XGBClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, classification_report
from sklearn.preprocessing import StandardScaler
from models.features import get_feature_columns
from config.settings import MODELS_DIR
from logs.logger import get_logger

log = get_logger("predictor")

class TradingPredictor:
    def __init__(self, symbol: str):
        self.symbol  = symbol
        self.model   = None
        self.scaler  = StandardScaler()
        self.trained = False
        self.model_path  = MODELS_DIR / f"{symbol}_model.pkl"
        self.scaler_path = MODELS_DIR / f"{symbol}_scaler.pkl"

    def train(self, df: pd.DataFrame) -> dict:
        """Entrena el modelo XGBoost con los datos históricos."""
        features = get_feature_columns()
        available = [f for f in features if f in df.columns]

        X = df[available].values
        y = df["target"].values

        if len(X) < 30:
            log.error(f"{self.symbol}: datos insuficientes para entrenar ({len(X)} filas)")
            return {}

        # Time Series Split — nunca mezcla futuro con pasado
        tscv  = TimeSeriesSplit(n_splits=3)
        scores = []

        for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
            X_train, X_val = X[train_idx], X[val_idx]
            y_train, y_val = y[train_idx], y[val_idx]

            scaler = StandardScaler()
            X_train_s = scaler.fit_transform(X_train)
            X_val_s   = scaler.transform(X_val)

            model = XGBClassifier(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                use_label_encoder=False,
                eval_metric="logloss",
                verbosity=0
            )
            model.fit(X_train_s, y_train)
            preds  = model.predict(X_val_s)
            acc    = accuracy_score(y_val, preds)
            scores.append(acc)
            log.info(f"{self.symbol} fold {fold+1}: accuracy={acc:.3f}")

        # Entrena modelo final con todos los datos
        self.scaler = StandardScaler()
        X_scaled    = self.scaler.fit_transform(X)
        self.model  = XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            use_label_encoder=False,
            eval_metric="logloss",
            verbosity=0
        )
        self.model.fit(X_scaled, y)
        self.trained     = True
        mean_acc         = np.mean(scores)

        # Guarda el modelo en disco
        self.save()

        result = {
            "symbol":       self.symbol,
            "accuracy_cv":  round(mean_acc, 4),
            "n_samples":    len(X),
            "n_features":   len(available),
            "features":     available
        }
        log.info(f"{self.symbol}: entrenado. Accuracy CV = {mean_acc:.3f}")
        return result

    def predict(self, df: pd.DataFrame) -> dict:
        """Genera señal para la última fila de datos."""
        if not self.trained:
            if self.model_path.exists():
                self.load()
            else:
                log.error(f"{self.symbol}: modelo no entrenado")
                return {"signal": "HOLD", "confidence": 0.0, "direction": 0}

        features  = get_feature_columns()
        available = [f for f in features if f in df.columns]
        last_row  = df[available].iloc[[-1]].values

        X_scaled  = self.scaler.transform(last_row)
        proba     = self.model.predict_proba(X_scaled)[0]
        direction = int(np.argmax(proba))
        confidence= float(np.max(proba))

        if confidence < 0.55:
            signal = "HOLD"
        elif direction == 1:
            signal = "BUY"
        else:
            signal = "SELL"

        result = {
            "symbol":     self.symbol,
            "signal":     signal,
            "direction":  direction,
            "confidence": round(confidence, 4),
            "prob_up":    round(float(proba[1]), 4),
            "prob_down":  round(float(proba[0]), 4)
        }
        log.info(f"{self.symbol}: señal={signal} confianza={confidence:.3f}")
        return result

    def save(self):
        with open(self.model_path,  "wb") as f: pickle.dump(self.model,  f)
        with open(self.scaler_path, "wb") as f: pickle.dump(self.scaler, f)
        log.info(f"{self.symbol}: modelo guardado en disco")

    def load(self):
        with open(self.model_path,  "rb") as f: self.model  = pickle.load(f)
        with open(self.scaler_path, "rb") as f: self.scaler = pickle.load(f)
        self.trained = True
        log.info(f"{self.symbol}: modelo cargado desde disco")
