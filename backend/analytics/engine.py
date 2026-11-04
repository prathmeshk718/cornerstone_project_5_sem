from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.analytics.constant_value import ConstantValueDetector
from backend.analytics.cross_validation import MultiSensorCrossValidator
from backend.analytics.ml_framework import MLConditionFramework
from backend.analytics.sudden_change import SuddenChangeDetector
from backend.config import SENSOR_CATALOG, SENSOR_IDS, ThresholdsConfig
from backend.models import (
    AlertDomain,
    AlertEvent,
    ConnectionStatus,
    EnvironmentalStatus,
    HealthStatus,
    SensorEvaluation,
)

STATUS_SEVERITY_RANK = {
    HealthStatus.HEALTHY: 0,
    HealthStatus.INSUFFICIENT_DATA: 1,
    HealthStatus.WARNING: 2,
    HealthStatus.MAINTENANCE_REQUIRED: 3,
    HealthStatus.DISCONNECTED: 4,
}


class ConditionMonitoringEngine:
    """
    Coordinates connectivity evaluation, warm-up gating, the 3 required sensor-analysis
    methods (Constant Value, Multi-Sensor Cross-Validation, Sudden Variation),
    the scikit-learn ML Condition Framework, and Environmental Hazard separation.
    """

    def __init__(self, thresholds: ThresholdsConfig) -> None:
        self.thresholds = thresholds
        self.constant_detector = ConstantValueDetector()
        self.cross_validator = MultiSensorCrossValidator()
        self.variation_detector = SuddenChangeDetector()
        self.ml_framework = MLConditionFramework()

        self._valid_sample_counts: Dict[str, int] = {sid: 0 for sid in SENSOR_IDS}
        self._was_arduino_connected: Optional[bool] = None
        self._was_sensor_valid: Dict[str, Optional[bool]] = {sid: None for sid in SENSOR_IDS}
        self._active_alert_signatures: Dict[Tuple[str, str], Tuple[str, float]] = {}

    def update_thresholds(self, thresholds: ThresholdsConfig) -> None:
        self.thresholds = thresholds

    def _emit_alert_if_needed(
        self,
        out_alerts: List[AlertEvent],
        active_keys_this_cycle: set,
        now_sec: float,
        ts_ms: int,
        ts_iso: str,
        sensor_id: str,
        sensor_name: str,
        domain: AlertDomain,
        category: str,
        severity: str,
        title: str,
        reason: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        sig_key = (sensor_id, category)
        active_keys_this_cycle.add(sig_key)

        prev = self._active_alert_signatures.get(sig_key)
        sev_rank = {"info": 1, "warning": 2, "critical": 3}

        should_emit = False
        if prev is None:
            should_emit = True
        else:
            prev_sev, prev_ts = prev
            # Emit immediately on severity escalation (e.g., Warning -> Maintenance Required)
            if sev_rank.get(severity, 1) > sev_rank.get(prev_sev, 1):
                should_emit = True
            # For active fire hazard, periodic reminder every 4x cooldown
            elif domain == AlertDomain.ENVIRONMENTAL and (now_sec - prev_ts) >= max(
                45.0, self.thresholds.alert_cooldown_sec * 3.0
            ):
                should_emit = True

        if should_emit:
            self._active_alert_signatures[sig_key] = (severity, now_sec)
            out_alerts.append(
                AlertEvent(
                    timestamp_ms=ts_ms,
                    timestamp_iso=ts_iso,
                    sensor_id=sensor_id,
                    sensor_name=sensor_name,
                    alert_domain=domain.value,
                    alert_category=category,
                    severity=severity,
                    title=title,
                    reason=reason,
                    details=details or {},
                )
            )

    @staticmethod
    def _merge_status(current: HealthStatus, candidate: HealthStatus) -> HealthStatus:
        if STATUS_SEVERITY_RANK[candidate] > STATUS_SEVERITY_RANK[current]:
            return candidate
        return current

    def evaluate_cycle(
        self,
        now_sec: float,
        ts_ms: int,
        arduino_connected: bool,
        sensor_inputs: Dict[str, Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], List[AlertEvent]]:
        """
        sensor_inputs format per sensor_id:
          {
            "value": Optional[float],
            "is_valid": bool,
            "age_sec": Optional[float],
            "extra": dict (e.g., {"intensity_pct": 6.4, "analog_raw": 958})
          }
        """
        cfg = self.thresholds
        ts_iso = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()
        new_alerts: List[AlertEvent] = []
        active_keys_this_cycle: set = set()

        # 1. Check Arduino Uno overall connectivity
        if not arduino_connected:
            self._emit_alert_if_needed(
                new_alerts,
                active_keys_this_cycle,
                now_sec,
                ts_ms,
                ts_iso,
                sensor_id="arduino_uno",
                sensor_name="Arduino Uno",
                domain=AlertDomain.CONNECTIVITY,
                category="Arduino Disconnection",
                severity="critical",
                title="Arduino Uno Disconnected – Communication Timeout",
                reason=(
                    f"No valid USB Serial / ROS 2 heartbeat received within "
                    f"{cfg.arduino_timeout_sec:.1f}s timeout. All sensor displays forced to 0."
                ),
            )
        self._was_arduino_connected = arduino_connected

        # Extract validity & values
        vals: Dict[str, Optional[float]] = {}
        valids: Dict[str, bool] = {}
        for sid in SENSOR_IDS:
            inp = sensor_inputs.get(sid, {})
            is_v = bool(arduino_connected and inp.get("is_valid") and inp.get("value") is not None)
            valids[sid] = is_v
            vals[sid] = float(inp["value"]) if is_v else None

            if not is_v:
                # Reset per-sensor warm-up & detector anchors on disconnect so stale readings never carry over
                self._valid_sample_counts[sid] = 0
                self.constant_detector.reset_sensor(sid)
                self.variation_detector.reset_sensor(sid)
                self.ml_framework.reset_sensor(sid)
                if arduino_connected:
                    # Individual sensor failed while Arduino Uno is still connected!
                    self._emit_alert_if_needed(
                        new_alerts,
                        active_keys_this_cycle,
                        now_sec,
                        ts_ms,
                        ts_iso,
                        sensor_id=sid,
                        sensor_name=SENSOR_CATALOG[sid]["name"],
                        domain=AlertDomain.CONNECTIVITY,
                        category="Sensor Disconnection",
                        severity="warning",
                        title=f"{SENSOR_CATALOG[sid]['name']} Disconnected / No Data",
                        reason=(
                            f"{SENSOR_CATALOG[sid]['name']} stopped transmitting valid readings "
                            f"(timeout > {cfg.sensor_timeout_sec:.1f}s) while Arduino Uno remains online. "
                            f"Display reset to 0; missing state recorded in database."
                        ),
                    )
            else:
                self._valid_sample_counts[sid] += 1
            self._was_sensor_valid[sid] = is_v

        # 2. Method 2: Multi-Sensor Correlation & Cross-Validation
        if arduino_connected:
            cv_res = self.cross_validator.evaluate(
                now_sec=now_sec,
                ds_val=vals["ds18b20_temp"],
                ds_valid=valids["ds18b20_temp"],
                dt_val=vals["dht11_temp"],
                dt_valid=valids["dht11_temp"],
                dh_val=vals["dht11_humidity"],
                dh_valid=valids["dht11_humidity"],
                flame_val=vals["flame_sensor"],
                flame_valid=valids["flame_sensor"],
                cfg=cfg,
            )
        else:
            self.cross_validator.reset()
            cv_res = self.cross_validator.evaluate(
                now_sec=now_sec,
                ds_val=None,
                ds_valid=False,
                dt_val=None,
                dt_valid=False,
                dh_val=None,
                dh_valid=False,
                flame_val=None,
                flame_valid=False,
                cfg=cfg,
            )

        if cv_res.fire_alert_triggered:
            self._emit_alert_if_needed(
                new_alerts,
                active_keys_this_cycle,
                now_sec,
                ts_ms,
                ts_iso,
                sensor_id="multi_sensor_env",
                sensor_name="Multi-Sensor Environment",
                domain=AlertDomain.ENVIRONMENTAL,
                category="Potential Fire Detection",
                severity="critical",
                title="Potential Fire Detected – Multi-Sensor Hazard Alert",
                reason=cv_res.environmental_reason,
            )

        # 3. Evaluate Methods 1 & 3 per connected sensor
        const_results = {}
        var_results = {}
        unchanged_durs: Dict[str, float] = {}

        for sid in SENSOR_IDS:
            if not valids[sid] or vals[sid] is None:
                unchanged_durs[sid] = 0.0
                continue

            extra = sensor_inputs.get(sid, {}).get("extra") or {}
            c_res = self.constant_detector.evaluate(
                sensor_id=sid,
                value=vals[sid],
                now_sec=now_sec,
                cfg=cfg,
                flame_intensity_pct=extra.get("intensity_pct"),
            )
            const_results[sid] = c_res
            unchanged_durs[sid] = c_res.unchanged_duration_sec

            v_res = self.variation_detector.evaluate(
                sensor_id=sid,
                value=vals[sid],
                now_sec=now_sec,
                cfg=cfg,
                corroborated_environmental_event=cv_res.corroborated_thermal_event,
            )
            var_results[sid] = v_res

        # 4. Run Hybrid scikit-learn ML Condition Monitoring Framework
        ml_summary = self.ml_framework.score(
            ds_val=vals["ds18b20_temp"],
            ds_valid=valids["ds18b20_temp"],
            dt_val=vals["dht11_temp"],
            dt_valid=valids["dht11_temp"],
            dh_val=vals["dht11_humidity"],
            dh_valid=valids["dht11_humidity"],
            flame_val=vals["flame_sensor"],
            flame_valid=valids["flame_sensor"],
            corroborated_fire=cv_res.fire_alert_triggered,
            unchanged_durations=unchanged_durs,
        )

        # 5. Synthesize final per-sensor evaluation & Sensor-Health Alerts
        evaluated_sensors: Dict[str, Dict[str, Any]] = {}
        for sid in SENSOR_IDS:
            meta = SENSOR_CATALOG[sid]
            inp = sensor_inputs.get(sid, {})
            extra = dict(inp.get("extra") or {})

            if not valids[sid] or vals[sid] is None:
                conn_status = (
                    ConnectionStatus.DISCONNECTED.value
                    if not arduino_connected
                    else ConnectionStatus.NO_DATA.value
                )
                eval_obj = SensorEvaluation(
                    sensor_id=sid,
                    name=meta["name"],
                    unit=meta["unit"],
                    display_value=0.0 if not meta["is_digital_state"] else 0,
                    raw_value=None,
                    is_valid=False,
                    connection_status=conn_status,
                    health_status=HealthStatus.DISCONNECTED.value,
                    health_reasons=[
                        "Arduino Uno disconnected."
                        if not arduino_connected
                        else "Sensor not transmitting valid data; display forced to 0."
                    ],
                    unchanged_duration_sec=0.0,
                    last_step_delta=0.0,
                    ml_anomaly_score=0.0,
                    ml_condition_label="Offline",
                    extra={"intensity_pct": 0.0} if sid == "flame_sensor" else {},
                )
                evaluated_sensors[sid] = eval_obj.to_dict()
                continue

            val = vals[sid]
            assert val is not None
            sample_count = self._valid_sample_counts[sid]
            c_res = const_results[sid]
            v_res = var_results[sid]
            ml_info = ml_summary["per_sensor"].get(sid, {"score": 0.05, "label": "Normal"})

            reasons: List[str] = []

            if sample_count < cfg.min_samples_for_health:
                final_health = HealthStatus.INSUFFICIENT_DATA
                reasons.append(
                    f"Collecting initial baseline ({sample_count}/{cfg.min_samples_for_health} samples)."
                )
            else:
                final_health = HealthStatus.HEALTHY

                # Method 1 contribution
                if c_res.status != HealthStatus.HEALTHY:
                    final_health = self._merge_status(final_health, c_res.status)
                    if c_res.reason:
                        reasons.append(c_res.reason)
                    cat = (
                        "Possible Sensor Maintenance Requirement"
                        if c_res.status == HealthStatus.MAINTENANCE_REQUIRED
                        else "Possible Stuck Sensor"
                    )
                    sev = "critical" if c_res.status == HealthStatus.MAINTENANCE_REQUIRED else "warning"
                    self._emit_alert_if_needed(
                        new_alerts,
                        active_keys_this_cycle,
                        now_sec,
                        ts_ms,
                        ts_iso,
                        sensor_id=sid,
                        sensor_name=meta["name"],
                        domain=AlertDomain.SENSOR_HEALTH,
                        category=cat,
                        severity=sev,
                        title=c_res.alert_title or "Sensor Health: Possible Maintenance Required",
                        reason=c_res.reason or "Constant sensor value persisted past threshold.",
                    )

                # Method 2 Cross-Validation contribution
                if sid in cv_res.sensor_overrides:
                    ov = cv_res.sensor_overrides[sid]
                    ov_status: HealthStatus = ov["status"]
                    final_health = self._merge_status(final_health, ov_status)
                    reasons.append(ov["reason"])
                    sev = "critical" if ov_status == HealthStatus.MAINTENANCE_REQUIRED else "warning"
                    self._emit_alert_if_needed(
                        new_alerts,
                        active_keys_this_cycle,
                        now_sec,
                        ts_ms,
                        ts_iso,
                        sensor_id=sid,
                        sensor_name=meta["name"],
                        domain=AlertDomain.SENSOR_HEALTH,
                        category=ov.get("alert_category", "Inconsistent Sensor Readings"),
                        severity=sev,
                        title=ov["alert_title"],
                        reason=ov["reason"],
                    )

                # Method 3 Sudden Variation contribution
                if v_res.status != HealthStatus.HEALTHY:
                    final_health = self._merge_status(final_health, v_res.status)
                    if v_res.reason:
                        reasons.append(v_res.reason)
                    sev = "critical" if v_res.status == HealthStatus.MAINTENANCE_REQUIRED else "warning"
                    self._emit_alert_if_needed(
                        new_alerts,
                        active_keys_this_cycle,
                        now_sec,
                        ts_ms,
                        ts_iso,
                        sensor_id=sid,
                        sensor_name=meta["name"],
                        domain=AlertDomain.SENSOR_HEALTH,
                        category="Abnormal Sensor Variation",
                        severity=sev,
                        title=v_res.alert_title or "Abnormal Sensor Variation – Inspection Recommended",
                        reason=v_res.reason or "Sudden reading jump exceeded expected variation.",
                    )

                # Hybrid ML Anomaly early-warning contribution (if deterministic rules haven't already escalated)
                if (
                    final_health == HealthStatus.HEALTHY
                    and ml_info["score"] >= cfg.ml_anomaly_warn_threshold
                    and not cv_res.fire_alert_triggered
                ):
                    final_health = HealthStatus.WARNING
                    reasons.append(
                        f"ML IsolationForest flagged multivariate anomaly ({ml_info['label']}, score={ml_info['score']:.2f})."
                    )

                if not reasons:
                    reasons.append("Sensor communicating and operating within expected parameters.")

            display_val = int(round(val)) if meta["is_digital_state"] else round(val, 2)
            eval_obj = SensorEvaluation(
                sensor_id=sid,
                name=meta["name"],
                unit=meta["unit"],
                display_value=display_val,
                raw_value=round(val, 3),
                is_valid=True,
                connection_status=ConnectionStatus.CONNECTED.value,
                health_status=final_health.value,
                health_reasons=reasons,
                unchanged_duration_sec=c_res.unchanged_duration_sec,
                last_step_delta=v_res.step_delta,
                ml_anomaly_score=ml_info["score"],
                ml_condition_label=ml_info["label"],
                extra=extra,
            )
            evaluated_sensors[sid] = eval_obj.to_dict()

        # Clear any alert signatures whose conditions are no longer active so future recurrences emit cleanly
        stale_keys = [k for k in self._active_alert_signatures if k not in active_keys_this_cycle]
        for k in stale_keys:
            del self._active_alert_signatures[k]

        snapshot = {
            "timestamp_ms": ts_ms,
            "timestamp_iso": ts_iso,
            "arduino_connected": arduino_connected,
            "environmental": {
                "status": cv_res.environmental_status.value,
                "is_fire_alert": cv_res.fire_alert_triggered,
                "reason": cv_res.environmental_reason,
            },
            "ml_summary": ml_summary,
            "sensors": evaluated_sensors,
        }
        return snapshot, new_alerts
