from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple

from backend.config import ThresholdsConfig
from backend.models import HealthStatus


@dataclass
class ConstantCheckResult:
    status: HealthStatus
    unchanged_duration_sec: float
    unchanged_samples: int
    alert_title: Optional[str] = None
    reason: Optional[str] = None


class ConstantValueDetector:
    """
    Method 1: Constant Value Detection.
    Monitors how long each sensor reports an unchanged reading using sensor-specific
    variation tolerances and observation windows.
    - Accounts for legitimate stable OFF (0) state on digital Flame sensors.
    - Uses peer-sensor movement (e.g., DHT11 changing while DS18B20 is frozen) to
      avoid false positives in calm rooms while catching stuck sensors reliably.
    """

    def __init__(self) -> None:
        self._anchor_value: Dict[str, float] = {}
        self._anchor_ts: Dict[str, float] = {}
        self._unchanged_count: Dict[str, int] = {}

        # Separate tracker for Flame analog intensity (A0) hardware freeze
        self._flame_analog_anchor: Optional[float] = None
        self._flame_analog_ts: Optional[float] = None
        self._flame_analog_count: int = 0

        # Track recent range of continuous sensors to cross-check environmental stability
        self._recent_values: Dict[str, Deque[Tuple[float, float]]] = {
            "ds18b20_temp": deque(maxlen=60),
            "dht11_temp": deque(maxlen=60),
            "dht11_humidity": deque(maxlen=60),
        }

    def reset_sensor(self, sensor_id: str) -> None:
        self._anchor_value.pop(sensor_id, None)
        self._anchor_ts.pop(sensor_id, None)
        self._unchanged_count.pop(sensor_id, None)
        if sensor_id == "flame_sensor":
            self._flame_analog_anchor = None
            self._flame_analog_ts = None
            self._flame_analog_count = 0
        if sensor_id in self._recent_values:
            self._recent_values[sensor_id].clear()

    def _peer_temp_delta(self, peer_id: str, since_ts: float) -> float:
        buf = self._recent_values.get(peer_id)
        if not buf or len(buf) < 3:
            return 0.0
        vals = [v for ts, v in buf if ts >= since_ts]
        if len(vals) < 2:
            return 0.0
        return max(vals) - min(vals)

    def evaluate(
        self,
        sensor_id: str,
        value: float,
        now_sec: float,
        cfg: ThresholdsConfig,
        flame_intensity_pct: Optional[float] = None,
    ) -> ConstantCheckResult:
        if sensor_id in self._recent_values:
            self._recent_values[sensor_id].append((now_sec, value))

        # Special handling for Digital Flame Sensor:
        # Digital 0 (No Flame) is the expected steady-state in safe environments!
        if sensor_id == "flame_sensor":
            if int(round(value)) == 0:
                # Check if analog IR line (A0) is frozen bit-for-bit over a long window
                if flame_intensity_pct is not None:
                    if (
                        self._flame_analog_anchor is None
                        or abs(flame_intensity_pct - self._flame_analog_anchor) > 0.01
                    ):
                        self._flame_analog_anchor = flame_intensity_pct
                        self._flame_analog_ts = now_sec
                        self._flame_analog_count = 1
                    else:
                        self._flame_analog_count += 1
                        dur = max(0.0, now_sec - (self._flame_analog_ts or now_sec))
                        if dur >= cfg.flame_analog_stuck_sec and self._flame_analog_count >= 15:
                            return ConstantCheckResult(
                                status=HealthStatus.MAINTENANCE_REQUIRED,
                                unchanged_duration_sec=round(dur, 1),
                                unchanged_samples=self._flame_analog_count,
                                alert_title="Sensor Health: Possible Maintenance Required",
                                reason=(
                                    f"Flame analog IR channel (A0) remained completely frozen at "
                                    f"{flame_intensity_pct:.1f}% for {dur:.1f}s ({self._flame_analog_count} samples)."
                                ),
                            )
                # Legitimate stable OFF state -> Healthy
                self._anchor_value[sensor_id] = value
                self._anchor_ts[sensor_id] = now_sec
                self._unchanged_count[sensor_id] = 1
                return ConstantCheckResult(
                    status=HealthStatus.HEALTHY,
                    unchanged_duration_sec=0.0,
                    unchanged_samples=1,
                )
            else:
                # Digital 1 (Flame ON): track duration; Method 2 cross-validates against heat,
                # while extreme constant latch also flags constant-ON behaviour if very prolonged
                if self._anchor_value.get(sensor_id) != 1.0:
                    self._anchor_value[sensor_id] = 1.0
                    self._anchor_ts[sensor_id] = now_sec
                    self._unchanged_count[sensor_id] = 1
                else:
                    self._unchanged_count[sensor_id] = self._unchanged_count.get(sensor_id, 1) + 1
                dur = max(0.0, now_sec - self._anchor_ts[sensor_id])
                return ConstantCheckResult(
                    status=HealthStatus.HEALTHY,
                    unchanged_duration_sec=round(dur, 1),
                    unchanged_samples=self._unchanged_count[sensor_id],
                )

        # Continuous environmental sensors: DS18B20 Temp, DHT11 Temp, DHT11 Humidity
        if sensor_id == "ds18b20_temp":
            eps = cfg.ds18b20_variation_eps
            warn_sec = cfg.ds18b20_stuck_warn_sec
            maint_sec = cfg.ds18b20_stuck_maint_sec
            peer_delta = self._peer_temp_delta(
                "dht11_temp", self._anchor_ts.get(sensor_id, now_sec)
            )
        elif sensor_id == "dht11_temp":
            eps = cfg.dht11_temp_variation_eps
            warn_sec = cfg.dht11_temp_stuck_warn_sec
            maint_sec = cfg.dht11_temp_stuck_maint_sec
            peer_delta = self._peer_temp_delta(
                "ds18b20_temp", self._anchor_ts.get(sensor_id, now_sec)
            )
        else:  # dht11_humidity
            eps = cfg.dht11_hum_variation_eps
            warn_sec = cfg.dht11_hum_stuck_warn_sec
            maint_sec = cfg.dht11_hum_stuck_maint_sec
            peer_delta = 0.0

        if (
            sensor_id not in self._anchor_value
            or abs(value - self._anchor_value[sensor_id]) > eps
        ):
            self._anchor_value[sensor_id] = value
            self._anchor_ts[sensor_id] = now_sec
            self._unchanged_count[sensor_id] = 1
            return ConstantCheckResult(
                status=HealthStatus.HEALTHY,
                unchanged_duration_sec=0.0,
                unchanged_samples=1,
            )

        self._unchanged_count[sensor_id] += 1
        duration = max(0.0, now_sec - self._anchor_ts[sensor_id])
        samples = self._unchanged_count[sensor_id]

        # If co-located peer temperature changed noticeably (>= 2.5C) while this sensor stayed
        # 100% frozen, tighten thresholds because room conditions are demonstrably changing
        effective_warn = warn_sec * 0.6 if peer_delta >= 2.5 else warn_sec
        effective_maint = maint_sec * 0.6 if peer_delta >= 2.5 else maint_sec

        if duration >= effective_maint and samples >= max(8, cfg.min_samples_for_health):
            peer_note = (
                f" Peer temperature sensor shifted by {peer_delta:.1f}°C during this window."
                if peer_delta >= 2.5
                else ""
            )
            return ConstantCheckResult(
                status=HealthStatus.MAINTENANCE_REQUIRED,
                unchanged_duration_sec=round(duration, 1),
                unchanged_samples=samples,
                alert_title="Sensor Health: Possible Maintenance Required",
                reason=(
                    f"Constant reading of {value:.2f} maintained unchanged for {duration:.1f}s "
                    f"({samples} samples), exceeding maintenance threshold ({effective_maint:.0f}s)."
                    f"{peer_note}"
                ),
            )

        if duration >= effective_warn and samples >= cfg.min_samples_for_health:
            return ConstantCheckResult(
                status=HealthStatus.WARNING,
                unchanged_duration_sec=round(duration, 1),
                unchanged_samples=samples,
                alert_title="Possible Stuck Sensor – Observation Active",
                reason=(
                    f"Reading has remained constant at {value:.2f} for {duration:.1f}s "
                    f"({samples} samples); monitoring for stuck-value fault."
                ),
            )

        return ConstantCheckResult(
            status=HealthStatus.HEALTHY,
            unchanged_duration_sec=round(duration, 1),
            unchanged_samples=samples,
        )
