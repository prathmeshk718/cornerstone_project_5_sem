from collections import deque
from typing import Any, Deque, Dict, Optional
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler


class MLConditionFramework:
    """
    Machine Learning Condition Monitoring & Predictive Maintenance Scorer.
    Uses a scikit-learn IsolationForest + StandardScaler trained on physics-informed
    multi-sensor feature vectors to score sensor condition degradation in real time
    while separating corroborated environmental hazards (e.g., genuine fire) from hardware faults.
    """

    def __init__(self, window_size: int = 12) -> None:
        self.window_size = window_size
        self._buffers: Dict[str, Deque[float]] = {
            "ds18b20_temp": deque(maxlen=window_size),
            "dht11_temp": deque(maxlen=window_size),
            "dht11_humidity": deque(maxlen=window_size),
            "flame_sensor": deque(maxlen=window_size),
        }

        self._scaler = StandardScaler()
        self._model = IsolationForest(
            n_estimators=100,
            contamination=0.04,
            random_state=42,
        )
        self._fit_baseline_model()

    def _fit_baseline_model(self) -> None:
        rng = np.random.default_rng(42)
        n_normal = 1000
        n_gradual_env = 200  # Normal HVAC ramps & corroborated environmental warming

        # Normal steady / gentle ambient behaviour
        ds_std_norm = rng.uniform(0.03, 0.45, size=n_normal)
        ds_step_norm = np.abs(rng.normal(0.08, 0.18, size=n_normal))
        dt_std_norm = rng.uniform(0.0, 0.65, size=n_normal)
        dt_step_norm = rng.choice([0.0, 0.1, 0.2, 0.5, 1.0], size=n_normal, p=[0.45, 0.2, 0.15, 0.15, 0.05])
        dh_step_norm = rng.choice([0.0, 0.5, 1.0, 1.5], size=n_normal, p=[0.5, 0.25, 0.2, 0.05])
        temp_res_norm = np.abs(rng.normal(0.6, 0.9, size=n_normal))
        flame_disc_norm = np.zeros(n_normal)

        x_norm = np.column_stack(
            [
                ds_std_norm,
                ds_step_norm,
                dt_std_norm,
                dt_step_norm,
                dh_step_norm,
                temp_res_norm,
                flame_disc_norm,
            ]
        )

        # Corroborated environmental ramp (gradual heating where both probes agree)
        ds_std_ramp = rng.uniform(0.4, 2.2, size=n_gradual_env)
        ds_step_ramp = rng.uniform(0.3, 2.5, size=n_gradual_env)
        dt_std_ramp = rng.uniform(0.4, 2.0, size=n_gradual_env)
        dt_step_ramp = rng.uniform(0.3, 2.5, size=n_gradual_env)
        dh_step_ramp = rng.uniform(0.2, 3.5, size=n_gradual_env)
        temp_res_ramp = rng.uniform(0.2, 3.2, size=n_gradual_env)
        flame_disc_ramp = np.zeros(n_gradual_env)

        x_ramp = np.column_stack(
            [
                ds_std_ramp,
                ds_step_ramp,
                dt_std_ramp,
                dt_step_ramp,
                dh_step_ramp,
                temp_res_ramp,
                flame_disc_ramp,
            ]
        )

        x_train = np.vstack([x_norm, x_ramp])
        x_scaled = self._scaler.fit_transform(x_train)
        self._model.fit(x_scaled)

    def reset_sensor(self, sensor_id: str) -> None:
        if sensor_id in self._buffers:
            self._buffers[sensor_id].clear()

    @staticmethod
    def _buf_stats(buf: Deque[float]) -> tuple[float, float]:
        if len(buf) < 2:
            return 0.1, 0.0
        arr = np.asarray(buf, dtype=float)
        return float(np.std(arr)), float(abs(arr[-1] - arr[-2]))

    def score(
        self,
        ds_val: Optional[float],
        ds_valid: bool,
        dt_val: Optional[float],
        dt_valid: bool,
        dh_val: Optional[float],
        dh_valid: bool,
        flame_val: Optional[float],
        flame_valid: bool,
        corroborated_fire: bool,
        unchanged_durations: Dict[str, float],
    ) -> Dict[str, Any]:
        if ds_valid and ds_val is not None:
            self._buffers["ds18b20_temp"].append(ds_val)
        if dt_valid and dt_val is not None:
            self._buffers["dht11_temp"].append(dt_val)
        if dh_valid and dh_val is not None:
            self._buffers["dht11_humidity"].append(dh_val)
        if flame_valid and flame_val is not None:
            self._buffers["flame_sensor"].append(flame_val)

        ds_std, ds_step = self._buf_stats(self._buffers["ds18b20_temp"])
        dt_std, dt_step = self._buf_stats(self._buffers["dht11_temp"])
        _, dh_step = self._buf_stats(self._buffers["dht11_humidity"])

        if ds_valid and dt_valid and ds_val is not None and dt_val is not None:
            # Discount cross-residual during corroborated fire due to natural probe thermal lag
            raw_res = abs(ds_val - dt_val)
            temp_res = min(raw_res, 2.0) if corroborated_fire else raw_res
        else:
            temp_res = 0.6

        flame_on = bool(flame_valid and flame_val is not None and int(round(flame_val)) == 1)
        flame_discord = 1.0 if (flame_on and not corroborated_fire) else 0.0

        # During corroborated fire, clamp step features to ramp domain so hardware health stays normal
        eff_ds_step = min(ds_step, 2.2) if corroborated_fire else ds_step
        eff_dt_step = min(dt_step, 2.2) if corroborated_fire else dt_step
        eff_dh_step = min(dh_step, 3.0) if corroborated_fire else dh_step

        feat = np.array(
            [[ds_std, eff_ds_step, dt_std, eff_dt_step, eff_dh_step, temp_res, flame_discord]],
            dtype=float,
        )
        feat_scaled = self._scaler.transform(feat)
        # decision_function: positive (~0.15) = inlier, negative = outlier
        raw_decision = float(self._model.decision_function(feat_scaled)[0])
        # Map decision_function smoothly to [0.0, 1.0] anomaly probability-like score
        system_score = float(np.clip(1.0 / (1.0 + np.exp(8.5 * (raw_decision - 0.01))), 0.02, 0.99))

        # Per-sensor decomposed anomaly scores + condition labels
        per_sensor: Dict[str, Dict[str, Any]] = {}

        # 1. DS18B20 ML score
        if not ds_valid:
            per_sensor["ds18b20_temp"] = {"score": 0.0, "label": "Offline"}
        else:
            stuck_boost = min(0.75, unchanged_durations.get("ds18b20_temp", 0.0) / 50.0) if ds_std < 0.01 else 0.0
            spike_boost = min(0.95, eff_ds_step / 8.0) if eff_ds_step > 3.5 else 0.0
            drift_boost = min(0.75, (temp_res - 4.0) / 10.0) if temp_res > 4.5 else 0.0
            s_ds = float(np.clip(max(0.06 + 0.25 * system_score, stuck_boost, spike_boost, drift_boost), 0.03, 0.99))
            lbl_ds = "Normal"
            if spike_boost >= 0.55:
                lbl_ds = "Spike / Noise Anomaly"
            elif stuck_boost >= 0.50:
                lbl_ds = "Zero-Variance / Stuck Pattern"
            elif drift_boost >= 0.45:
                lbl_ds = "Cross-Sensor Drift"
            per_sensor["ds18b20_temp"] = {"score": round(s_ds, 3), "label": lbl_ds}

        # 2. DHT11 Temp ML score
        if not dt_valid:
            per_sensor["dht11_temp"] = {"score": 0.0, "label": "Offline"}
        else:
            stuck_boost = min(0.75, unchanged_durations.get("dht11_temp", 0.0) / 85.0) if dt_std < 0.01 else 0.0
            spike_boost = min(0.95, eff_dt_step / 8.0) if eff_dt_step > 3.5 else 0.0
            drift_boost = min(0.75, (temp_res - 4.0) / 10.0) if temp_res > 4.5 else 0.0
            s_dt = float(np.clip(max(0.06 + 0.25 * system_score, stuck_boost, spike_boost, drift_boost), 0.03, 0.99))
            lbl_dt = "Normal"
            if spike_boost >= 0.55:
                lbl_dt = "Spike / Noise Anomaly"
            elif stuck_boost >= 0.50:
                lbl_dt = "Zero-Variance / Stuck Pattern"
            elif drift_boost >= 0.45:
                lbl_dt = "Cross-Sensor Drift"
            per_sensor["dht11_temp"] = {"score": round(s_dt, 3), "label": lbl_dt}

        # 3. DHT11 Humidity ML score
        if not dh_valid:
            per_sensor["dht11_humidity"] = {"score": 0.0, "label": "Offline"}
        else:
            stuck_boost = min(0.75, unchanged_durations.get("dht11_humidity", 0.0) / 85.0)
            spike_boost = min(0.95, eff_dh_step / 18.0) if eff_dh_step > 10.0 else 0.0
            s_dh = float(np.clip(max(0.05 + 0.20 * system_score, stuck_boost if len(self._buffers["dht11_humidity"]) >= 10 else 0.0, spike_boost), 0.03, 0.99))
            lbl_dh = "Normal"
            if spike_boost >= 0.55:
                lbl_dh = "Humidity Jump Anomaly"
            elif stuck_boost >= 0.50:
                lbl_dh = "Zero-Variance / Stuck Pattern"
            per_sensor["dht11_humidity"] = {"score": round(s_dh, 3), "label": lbl_dh}

        # 4. Flame Sensor ML score
        if not flame_valid:
            per_sensor["flame_sensor"] = {"score": 0.0, "label": "Offline"}
        else:
            s_fl = 0.88 if flame_discord > 0.5 else 0.05
            lbl_fl = "Thermal Discordance Anomaly" if flame_discord > 0.5 else "Normal"
            per_sensor["flame_sensor"] = {"score": round(s_fl, 3), "label": lbl_fl}

        effective_system_score = round(
            max([system_score] + [v["score"] for v in per_sensor.values()]), 3
        )
        if corroborated_fire and flame_discord == 0.0:
            effective_system_score = round(min(effective_system_score, 0.22), 3)

        return {
            "model_name": "IsolationForest Multivariate Condition Monitor (scikit-learn)",
            "system_anomaly_score": effective_system_score,
            "maintenance_risk_pct": round(effective_system_score * 100.0, 1),
            "per_sensor": per_sensor,
        }
