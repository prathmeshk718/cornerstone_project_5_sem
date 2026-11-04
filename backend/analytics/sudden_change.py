from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Deque, Dict, Optional, Tuple

from backend.config import ThresholdsConfig
from backend.models import HealthStatus


@dataclass
class VariationCheckResult:
    status: HealthStatus
    step_delta: float
    rate_per_sec: float
    spike_count_in_window: int
    alert_title: Optional[str] = None
    reason: Optional[str] = None


class SuddenChangeDetector:
    """
    Method 3: Sudden Change and Abnormal Variation Detection.
    Evaluates step-to-step delta, rate-of-change (dx/dt), rolling median baseline,
    and sensor-specific physical operating bounds.
    Evaluates environmental corroboration before attributing moderate rapid shifts to hardware fault.
    """

    def __init__(self) -> None:
        self._history: Dict[str, Deque[Tuple[float, float]]] = {
            "ds18b20_temp": deque(maxlen=60),
            "dht11_temp": deque(maxlen=60),
            "dht11_humidity": deque(maxlen=60),
        }
        self._spike_timestamps: Dict[str, Deque[float]] = {
            "ds18b20_temp": deque(maxlen=30),
            "dht11_temp": deque(maxlen=30),
            "dht11_humidity": deque(maxlen=30),
        }
        self._last_spike_detail: Dict[str, str] = {}

    def reset_sensor(self, sensor_id: str) -> None:
        if sensor_id in self._history:
            self._history[sensor_id].clear()
        if sensor_id in self._spike_timestamps:
            self._spike_timestamps[sensor_id].clear()
        self._last_spike_detail.pop(sensor_id, None)

    def evaluate(
        self,
        sensor_id: str,
        value: float,
        now_sec: float,
        cfg: ThresholdsConfig,
        corroborated_environmental_event: bool = False,
    ) -> VariationCheckResult:
        # Digital flame sensor ON/OFF transitions (0 -> 1) are expected binary events, not analog spikes
        if sensor_id == "flame_sensor":
            return VariationCheckResult(
                status=HealthStatus.HEALTHY,
                step_delta=0.0,
                rate_per_sec=0.0,
                spike_count_in_window=0,
            )

        hist = self._history[sensor_id]
        spikes = self._spike_timestamps[sensor_id]

        # Prune old spike timestamps outside rolling observation window (~45s)
        while spikes and (now_sec - spikes[0]) > 45.0:
            spikes.popleft()

        if sensor_id == "ds18b20_temp":
            max_step = cfg.ds18b20_max_step_c
            unit = "°C"
            phys_min, phys_max = -55.0, 125.0
        elif sensor_id == "dht11_temp":
            max_step = cfg.dht11_temp_max_step_c
            unit = "°C"
            phys_min, phys_max = 0.0, 55.0
        else:  # dht11_humidity
            max_step = cfg.dht11_hum_max_step_pct
            unit = "%"
            phys_min, phys_max = 10.0, 100.0

        if not hist:
            hist.append((now_sec, value))
            return VariationCheckResult(
                status=HealthStatus.HEALTHY,
                step_delta=0.0,
                rate_per_sec=0.0,
                spike_count_in_window=0,
            )

        prev_ts, prev_val = hist[-1]
        dt = max(0.2, now_sec - prev_ts)
        step_delta = value - prev_val
        abs_step = abs(step_delta)
        rate = abs_step / dt

        # Compute robust rolling median over recent clean window
        window_vals = [v for _, v in list(hist)[-cfg.variation_window_samples :]]
        rolling_med = float(median(window_vals)) if window_vals else prev_val

        hist.append((now_sec, value))

        # Check physical out-of-range reading (e.g., electrical glitch)
        out_of_bounds = value < phys_min or value > phys_max

        # Evaluate whether a large step is a genuine corroborated environmental event:
        # Moderate rapid rise (up to 2x max_step) during an active corroborated fire is physically plausible,
        # whereas extreme jumps (e.g., 34°C -> 95°C = +61°C in 1s) violate sensor thermal inertia regardless.
        effective_max_step = (
            max_step * 2.0
            if (corroborated_environmental_event and step_delta > 0 and sensor_id != "dht11_humidity")
            else max_step
        )

        # Also detect DS18B20 classic 85.0°C scratchpad reset spike if previous reading wasn't near 85°C
        ds_85_glitch = (
            sensor_id == "ds18b20_temp"
            and abs(value - 85.0) < 0.05
            and abs(prev_val - 85.0) > 8.0
        )

        is_new_spike = out_of_bounds or ds_85_glitch or (abs_step > effective_max_step)

        if is_new_spike:
            spikes.append(now_sec)
            if out_of_bounds:
                self._last_spike_detail[sensor_id] = (
                    f"Reading {value:.1f}{unit} exceeded sensor operating bounds "
                    f"[{phys_min:.0f}{unit}, {phys_max:.0f}{unit}]."
                )
            elif ds_85_glitch:
                self._last_spike_detail[sensor_id] = (
                    f"Sudden jump from {prev_val:.1f}°C to 85.0°C (classic DS18B20 power-reset scratchpad fault)."
                )
            else:
                self._last_spike_detail[sensor_id] = (
                    f"Sudden jump from {prev_val:.1f}{unit} to {value:.1f}{unit} "
                    f"(Δ{step_delta:+.1f}{unit} in {dt:.1f}s, rate {rate:.1f}{unit}/s > max {effective_max_step:.1f}{unit})."
                )

        spike_count = len(spikes)
        recent_spike_age = (now_sec - spikes[-1]) if spikes else 9999.0

        # Also detect if sensor jumped earlier and remains perched far away from pre-spike median uncorroborated
        sustained_outlier = (
            not corroborated_environmental_event
            and spike_count >= 1
            and abs(value - rolling_med) > (max_step * 2.0)
        )

        if spike_count >= cfg.spikes_for_maintenance or (
            spike_count >= 1 and recent_spike_age <= cfg.abnormal_spike_hold_sec and out_of_bounds
        ):
            return VariationCheckResult(
                status=HealthStatus.MAINTENANCE_REQUIRED,
                step_delta=round(step_delta, 2),
                rate_per_sec=round(rate, 2),
                spike_count_in_window=spike_count,
                alert_title="Abnormal Sensor Variation – Inspection Recommended",
                reason=(
                    f"{self._last_spike_detail.get(sensor_id, 'Repeated abnormal spikes detected.')} "
                    f"({spike_count} abnormal variations recorded in observation window)."
                ),
            )

        if recent_spike_age <= cfg.abnormal_spike_hold_sec or sustained_outlier:
            return VariationCheckResult(
                status=HealthStatus.WARNING,
                step_delta=round(step_delta, 2),
                rate_per_sec=round(rate, 2),
                spike_count_in_window=spike_count,
                alert_title="Abnormal Sensor Variation – Inspection Recommended",
                reason=self._last_spike_detail.get(
                    sensor_id,
                    f"Abnormal variation detected (current {value:.1f}{unit}, Δ{step_delta:+.1f}{unit}).",
                ),
            )

        return VariationCheckResult(
            status=HealthStatus.HEALTHY,
            step_delta=round(step_delta, 2),
            rate_per_sec=round(rate, 2),
            spike_count_in_window=spike_count,
        )
