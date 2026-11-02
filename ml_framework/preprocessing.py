from collections import deque
from typing import Dict, Any, List, Tuple, Optional

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler, MinMaxScaler


FEATURE_COLUMNS = [
    "ds18b20_temp_c",
    "dht11_temp_c",
    "dht11_humidity_pct",
    "yg1006_flame",
    "smoke_raw_adc",
    "temp_differential_c",
    "ds18b20_smoothed",
    "ds18b20_rolling_std",
    "smoke_smoothed",
    "smoke_baseline_ratio",
]

PHYSICAL_BOUNDS = {
    "ds18b20_temp_c": (-55.0, 125.0, 40.0),      # (min, max, fallback_default)
    "dht11_temp_c": (0.0, 55.0, 26.0),
    "dht11_humidity_pct": (10.0, 99.0, 48.0),
    "yg1006_flame": (0.0, 1.0, 0.0),
    "smoke_raw_adc": (0.0, 1023.0, 160.0),
}


class SensorDataPreprocessor:
    """
    Implements Section 6.A (Data Preprocessing):
      1. Data cleaning (physical range enforcement & outlier sanitization)
      2. Missing-value handling (forward-fill + median imputation)
      3. Noise handling (Exponential Moving Average & rolling window smoothing)
      4. Feature extraction (maintaining independent DS18B20 & DHT11 readings +
         engineered thermal gradient and smoke ratio features)
      5. Data normalization and standardization (StandardScaler & MinMaxScaler)
      6. Dataset preparation for model training and real-time inference
    """

    def __init__(self, window_size: int = 8, ema_alpha: float = 0.35):
        self.window_size = window_size
        self.ema_alpha = ema_alpha
        self.standard_scaler = StandardScaler()
        self.minmax_scaler = MinMaxScaler()
        self.is_fitted = False

        # Rolling buffer for real-time streaming preprocessing
        self._stream_buffer: deque = deque(maxlen=window_size)
        self._last_valid: Dict[str, float] = {
            k: v[2] for k, v in PHYSICAL_BOUNDS.items()
        }

    def clean_and_impute_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Cleans batch CSV data, replaces out-of-range / NaN values via forward-fill
        and median imputation, and applies noise smoothing.
        """
        df_clean = df.copy()

        for col, (lo, hi, default_val) in PHYSICAL_BOUNDS.items():
            if col not in df_clean.columns:
                df_clean[col] = default_val
            df_clean[col] = pd.to_numeric(df_clean[col], errors="coerce")
            # Mark out-of-range spikes as NaN so they are imputed cleanly
            df_clean.loc[(df_clean[col] < lo) | (df_clean[col] > hi), col] = np.nan
            # Forward-fill then backward-fill then median/default fallback
            median_val = df_clean[col].median()
            fill_val = default_val if pd.isna(median_val) else float(median_val)
            df_clean[col] = (
                df_clean[col].ffill().bfill().fillna(fill_val)
            )

        return df_clean

    def extract_features_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Extracts domain-specific condition monitoring features from cleaned sensor data.
        """
        df_feat = self.clean_and_impute_dataframe(df)

        # Independent temperature differential: Equipment Temp - Ambient Temp
        df_feat["temp_differential_c"] = (
            df_feat["ds18b20_temp_c"] - df_feat["dht11_temp_c"]
        ).round(3)

        # Noise handling: Exponential Moving Average (EMA) smoothing
        df_feat["ds18b20_smoothed"] = (
            df_feat["ds18b20_temp_c"]
            .ewm(alpha=self.ema_alpha, adjust=False)
            .mean()
            .round(3)
        )
        df_feat["ds18b20_rolling_std"] = (
            df_feat["ds18b20_temp_c"]
            .rolling(window=min(5, len(df_feat)), min_periods=1)
            .std()
            .fillna(0.0)
            .round(3)
        )
        df_feat["smoke_smoothed"] = (
            df_feat["smoke_raw_adc"]
            .ewm(alpha=self.ema_alpha, adjust=False)
            .mean()
            .round(2)
        )
        df_feat["smoke_baseline_ratio"] = (
            df_feat["smoke_raw_adc"] / 160.0
        ).round(3)

        return df_feat

    def prepare_training_dataset(
        self, df: pd.DataFrame, target_col: str = "operator_label"
    ) -> Tuple[np.ndarray, np.ndarray, pd.DataFrame]:
        """
        Prepares standardized feature matrix X and target vector y for ML training.
        """
        df_feat = self.extract_features_dataframe(df)
        X_raw = df_feat[FEATURE_COLUMNS].values.astype(np.float64)

        X_scaled = self.standard_scaler.fit_transform(X_raw)
        self.minmax_scaler.fit(X_raw)
        self.is_fitted = True

        if target_col in df_feat.columns:
            y = pd.to_numeric(df_feat[target_col], errors="coerce").fillna(0).astype(int).values
        else:
            y = np.zeros(len(df_feat), dtype=int)

        return X_scaled, y, df_feat

    def process_realtime_sample(
        self, raw_sample: Dict[str, Any]
    ) -> Tuple[Dict[str, float], np.ndarray]:
        """
        Cleans, imputes, smooths, extracts features, and normalizes a single real-time
        sensor frame from Arduino / ROS 2.
        """
        cleaned: Dict[str, float] = {}
        for col, (lo, hi, default_val) in PHYSICAL_BOUNDS.items():
            val = raw_sample.get(col, None)
            try:
                fval = float(val)
                if np.isnan(fval) or fval < lo or fval > hi:
                    fval = self._last_valid.get(col, default_val)
                else:
                    self._last_valid[col] = fval
            except (TypeError, ValueError):
                fval = self._last_valid.get(col, default_val)
            cleaned[col] = fval

        self._stream_buffer.append(cleaned)

        ds_vals = [item["ds18b20_temp_c"] for item in self._stream_buffer]
        smoke_vals = [item["smoke_raw_adc"] for item in self._stream_buffer]

        # EMA / Rolling smoothing on streaming window
        ds_smoothed = float(np.mean(ds_vals[-4:]))
        ds_std = float(np.std(ds_vals)) if len(ds_vals) > 1 else 0.0
        smoke_smoothed = float(np.mean(smoke_vals[-4:]))
        temp_diff = cleaned["ds18b20_temp_c"] - cleaned["dht11_temp_c"]
        smoke_ratio = cleaned["smoke_raw_adc"] / 160.0

        features = {
            "ds18b20_temp_c": round(cleaned["ds18b20_temp_c"], 2),
            "dht11_temp_c": round(cleaned["dht11_temp_c"], 2),
            "dht11_humidity_pct": round(cleaned["dht11_humidity_pct"], 1),
            "yg1006_flame": float(int(cleaned["yg1006_flame"])),
            "smoke_raw_adc": round(cleaned["smoke_raw_adc"], 1),
            "temp_differential_c": round(temp_diff, 2),
            "ds18b20_smoothed": round(ds_smoothed, 2),
            "ds18b20_rolling_std": round(ds_std, 3),
            "smoke_smoothed": round(smoke_smoothed, 1),
            "smoke_baseline_ratio": round(smoke_ratio, 3),
        }

        vec = np.array([[features[col] for col in FEATURE_COLUMNS]], dtype=np.float64)
        if self.is_fitted:
            vec_scaled = self.standard_scaler.transform(vec)
        else:
            vec_scaled = vec

        return features, vec_scaled
