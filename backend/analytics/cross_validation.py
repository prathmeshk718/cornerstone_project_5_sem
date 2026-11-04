from collections import deque
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Deque, Dict, Optional

from backend.config import ThresholdsConfig
from backend.models import EnvironmentalStatus, HealthStatus


@dataclass
class CrossValidationResult:
    environmental_status: EnvironmentalStatus
    environmental_reason: str
    fire_alert_triggered: bool = False
    corroborated_thermal_event: bool = False
    sensor_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class MultiSensorCrossValidator:
    """
    Method 2: Multi-Sensor Correlation and Cross-Validation.
    Separates genuine environmental fire hazards from inconsistent/faulty sensors:
      - Flame ON + Thermal Rise (with optional Humidity Drop) -> Potential Fire Alert (Environmental)
      - Persistent Flame ON + Stable Ambient Temps -> Possible Flame Sensor Fault (Sensor Health)
      - Persistent divergence between DS18B20 and DHT11 Temp -> Inconsistent Temperature Readings
    """

    def __init__(self) -> None:
        self._ds_baseline_buf: Deque[float] = deque(maxlen=40)
        self._dt_baseline_buf: Deque[float] = deque(maxlen=40)
        self._dh_baseline_buf: Deque[float] = deque(maxlen=40)

        # Snapshot of ambient baselines captured right before flame turns ON
        self._pre_flame_ds_base: Optional[float] = None
        self._pre_flame_dt_base: Optional[float] = None
        self._pre_flame_dh_base: Optional[float] = None

        self._flame_active_since: Optional[float] = None
        self._temp_divergence_since: Optional[float] = None

    def reset(self) -> None:
        self._ds_baseline_buf.clear()
        self._dt_baseline_buf.clear()
        self._dh_baseline_buf.clear()
        self._pre_flame_ds_base = None
        self._pre_flame_dt_base = None
        self._pre_flame_dh_base = None
        self._flame_active_since = None
        self._temp_divergence_since = None

    @staticmethod
    def _safe_median(buf: Deque[float], window: int) -> Optional[float]:
        if not buf:
            return None
        items = list(buf)[-window:]
        return float(median(items)) if items else None

    def evaluate(
        self,
        now_sec: float,
        ds_val: Optional[float],
        ds_valid: bool,
        dt_val: Optional[float],
        dt_valid: bool,
        dh_val: Optional[float],
        dh_valid: bool,
        flame_val: Optional[float],
        flame_valid: bool,
        cfg: ThresholdsConfig,
    ) -> CrossValidationResult:
        flame_on = bool(flame_valid and flame_val is not None and int(round(flame_val)) == 1)

        # Update rolling ambient buffers when NOT in an active flame condition
        if not flame_on:
            if ds_valid and ds_val is not None and -40.0 <= ds_val <= 65.0:
                self._ds_baseline_buf.append(ds_val)
            if dt_valid and dt_val is not None and -10.0 <= dt_val <= 55.0:
                self._dt_baseline_buf.append(dt_val)
            if dh_valid and dh_val is not None and 5.0 <= dh_val <= 99.0:
                self._dh_baseline_buf.append(dh_val)

            self._flame_active_since = None
            self._pre_flame_ds_base = None
            self._pre_flame_dt_base = None
            self._pre_flame_dh_base = None
        else:
            if self._flame_active_since is None:
                self._flame_active_since = now_sec
                self._pre_flame_ds_base = self._safe_median(
                    self._ds_baseline_buf, cfg.baseline_window_samples
                )
                self._pre_flame_dt_base = self._safe_median(
                    self._dt_baseline_buf, cfg.baseline_window_samples
                )
                self._pre_flame_dh_base = self._safe_median(
                    self._dh_baseline_buf, cfg.baseline_window_samples
                )

        ds_base = self._pre_flame_ds_base or self._safe_median(
            self._ds_baseline_buf, cfg.baseline_window_samples
        )
        dt_base = self._pre_flame_dt_base or self._safe_median(
            self._dt_baseline_buf, cfg.baseline_window_samples
        )
        dh_base = self._pre_flame_dh_base or self._safe_median(
            self._dh_baseline_buf, cfg.baseline_window_samples
        )

        ds_rise = (ds_val - ds_base) if (ds_valid and ds_val is not None and ds_base is not None) else 0.0
        dt_rise = (dt_val - dt_base) if (dt_valid and dt_val is not None and dt_base is not None) else 0.0
        dh_drop = (dh_base - dh_val) if (dh_valid and dh_val is not None and dh_base is not None) else 0.0

        ds_hot = bool(
            ds_valid
            and ds_val is not None
            and (
                ds_rise >= cfg.fire_temp_rise_threshold_c
                or ds_val >= cfg.fire_abs_temp_threshold_c
            )
        )
        dt_hot = bool(
            dt_valid
            and dt_val is not None
            and (
                dt_rise >= cfg.fire_temp_rise_threshold_c
                or dt_val >= cfg.fire_abs_temp_threshold_c
            )
        )
        both_temps_available = bool(ds_valid and dt_valid and ds_val is not None and dt_val is not None)
        any_temp_available = bool(
            (ds_valid and ds_val is not None) or (dt_valid and dt_val is not None)
        )

        # Require plausible thermal agreement if both temp sensors are online:
        # e.g., a single-sample electrical spike to 95C on DS18B20 alone is NOT real fire,
        # whereas gradual/sustained heating on one or both sensors alongside flame IS real fire.
        thermal_corroborated = False
        if both_temps_available:
            # Either both show notable warming, or one shows warming while the other is also trending up (>=0.8C)
            if (ds_hot and dt_hot) or (ds_hot and dt_rise >= 0.8) or (dt_hot and ds_rise >= 0.8):
                thermal_corroborated = True
            elif (ds_val >= cfg.fire_abs_temp_threshold_c and dt_val >= cfg.fire_abs_temp_threshold_c - 5.0):
                thermal_corroborated = True
        elif any_temp_available:
            thermal_corroborated = ds_hot or dt_hot

        overrides: Dict[str, Dict[str, Any]] = {}
        env_status = EnvironmentalStatus.NORMAL
        env_reason = "Environmental readings within normal ambient bounds."
        fire_alert = False

        # --- Evaluation 1: Flame Sensor Active ---
        if flame_on:
            flame_duration = max(0.0, now_sec - (self._flame_active_since or now_sec))

            if thermal_corroborated:
                # GENUINE ENVIRONMENTAL FIRE CONDITION (Not a sensor fault!)
                env_status = EnvironmentalStatus.FIRE_HAZARD
                fire_alert = True
                hum_clause = (
                    f", DHT11 humidity dropped by {dh_drop:.1f}% (now {dh_val:.1f}%)"
                    if (dh_valid and dh_val is not None and dh_drop >= cfg.fire_humidity_drop_hint_pct)
                    else (
                        f", DHT11 humidity at {dh_val:.1f}%"
                        if (dh_valid and dh_val is not None)
                        else ""
                    )
                )
                temp_parts = []
                if ds_valid and ds_val is not None:
                    temp_parts.append(f"DS18B20={ds_val:.1f}°C (Δ+{ds_rise:.1f}°C)")
                if dt_valid and dt_val is not None:
                    temp_parts.append(f"DHT11={dt_val:.1f}°C (Δ+{dt_rise:.1f}°C)")
                env_reason = (
                    f"Flame detected with corroborated thermal rise ({', '.join(temp_parts)}{hum_clause})."
                )
            elif any_temp_available:
                # Flame reported ON, yet temperature sensors show NO corroborating heat!
                if flame_duration >= cfg.flame_fault_maint_sec:
                    overrides["flame_sensor"] = {
                        "status": HealthStatus.MAINTENANCE_REQUIRED,
                        "alert_title": "Possible Flame Sensor Fault – Sensor Readings Inconsistent",
                        "alert_category": "Inconsistent Sensor Readings",
                        "reason": (
                            f"Flame sensor continuously reports flame for {flame_duration:.1f}s, "
                            f"but thermal sensors show no significant rise "
                            f"(DS18B20 Δ{ds_rise:+.1f}°C, DHT11 Δ{dt_rise:+.1f}°C)."
                        ),
                    }
                    env_reason = (
                        "Flame trigger uncorroborated by thermal sensors; isolated as likely flame sensor fault."
                    )
                elif flame_duration >= cfg.flame_validation_window_sec:
                    overrides["flame_sensor"] = {
                        "status": HealthStatus.WARNING,
                        "alert_title": "Possible Flame Sensor Fault – Sensor Readings Inconsistent",
                        "alert_category": "Inconsistent Sensor Readings",
                        "reason": (
                            f"Flame sensor active for {flame_duration:.1f}s without corroborating "
                            f"temperature increase on DS18B20/DHT11."
                        ),
                    }
                    env_reason = "Evaluating uncorroborated flame signal against stable room temperature."
                else:
                    env_status = EnvironmentalStatus.ELEVATED_WATCH
                    env_reason = (
                        f"Transient flame signal ({flame_duration:.1f}s); awaiting thermal cross-validation."
                    )
            else:
                # Flame ON while BOTH temperature sensors are offline -> warn cautiously
                env_status = EnvironmentalStatus.ELEVATED_WATCH
                env_reason = (
                    "Flame sensor triggered, but both DS18B20 and DHT11 temperature sensors are offline "
                    "for thermal cross-validation."
                )

        # --- Evaluation 2: Dual Temperature Cross-Validation (DS18B20 vs DHT11 Temp) ---
        if both_temps_available and ds_val is not None and dt_val is not None:
            temp_diff = abs(ds_val - dt_val)
            # Allow larger transient difference during an actively corroborated fire due to probe lag
            allowed_diff = (
                cfg.max_temp_sensor_divergence_c * 2.0
                if env_status == EnvironmentalStatus.FIRE_HAZARD
                else cfg.max_temp_sensor_divergence_c
            )
            if temp_diff > allowed_diff:
                if self._temp_divergence_since is None:
                    self._temp_divergence_since = now_sec
                div_dur = max(0.0, now_sec - self._temp_divergence_since)
                if div_dur >= cfg.temp_divergence_persist_sec:
                    # Identify which sensor deviated more strongly from its pre-drift baseline
                    suspect_id = (
                        "ds18b20_temp" if abs(ds_rise) >= abs(dt_rise) else "dht11_temp"
                    )
                    sev_status = (
                        HealthStatus.MAINTENANCE_REQUIRED
                        if div_dur >= cfg.temp_divergence_persist_sec * 2.5
                        else HealthStatus.WARNING
                    )
                    overrides[suspect_id] = {
                        "status": sev_status,
                        "alert_title": "Inconsistent Sensor Readings – Temperature Mismatch",
                        "alert_category": "Inconsistent Sensor Readings",
                        "reason": (
                            f"DS18B20 ({ds_val:.1f}°C) and DHT11 ({dt_val:.1f}°C) differ by "
                            f"{temp_diff:.1f}°C for {div_dur:.1f}s (threshold {allowed_diff:.1f}°C)."
                        ),
                    }
            else:
                self._temp_divergence_since = None

            # Also note dual-sensor rapid heat watch even if flame is not in optical line-of-sight
            if not flame_on and ds_hot and dt_hot:
                env_status = EnvironmentalStatus.ELEVATED_WATCH
                env_reason = (
                    f"Both DS18B20 ({ds_val:.1f}°C) and DHT11 ({dt_val:.1f}°C) indicate elevated heat "
                    f"without optical flame trigger."
                )
        else:
            self._temp_divergence_since = None

        return CrossValidationResult(
            environmental_status=env_status,
            environmental_reason=env_reason,
            fire_alert_triggered=fire_alert,
            corroborated_thermal_event=bool(
                thermal_corroborated or (both_temps_available and ds_hot and dt_hot)
            ),
            sensor_overrides=overrides,
        )
