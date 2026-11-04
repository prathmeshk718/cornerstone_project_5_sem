import math
import random
import time
from typing import Any, Dict, List

SCENARIO_CATALOG: List[Dict[str, str]] = [
    {
        "id": "normal",
        "label": "Normal Operation",
        "description": "All 3 physical sensors healthy with natural room micro-fluctuations (~28.5°C, 54% RH, no flame).",
    },
    {
        "id": "arduino_disconnected",
        "label": "Disconnect Arduino Uno",
        "description": "Simulates unplugging USB cable: heartbeat stops, all readings show 0, stored as missing (NULL).",
    },
    {
        "id": "sensor_disconnect_ds18b20",
        "label": "Disconnect DS18B20 Only",
        "description": "DS18B20 wire unplugged -> displays 0°C ('No Data'), while DHT11 & Flame continue operating.",
    },
    {
        "id": "sensor_disconnect_dht11",
        "label": "Disconnect DHT11 Only",
        "description": "DHT11 stops responding -> DHT11 Temp & Humidity show 0 ('No Data'), DS18B20 & Flame continue.",
    },
    {
        "id": "stuck_ds18b20",
        "label": "Method 1: Stuck DS18B20",
        "description": "DS18B20 freezes at exact constant 28.75°C while room shifts -> triggers 'Possible Maintenance Required'.",
    },
    {
        "id": "false_flame_fault",
        "label": "Method 2a: False Flame Fault",
        "description": "Flame sensor reports ON continuously while DS18B20 & DHT11 stay cool -> 'Sensor Readings Inconsistent' (no fire alarm).",
    },
    {
        "id": "genuine_fire",
        "label": "Method 2b: Genuine Fire Hazard",
        "description": "Flame ON + corroborated DS18B20 & DHT11 heat rise + humidity drop -> 'Potential Fire Detected' (sensors stay Healthy).",
    },
    {
        "id": "sudden_spike_ds18b20",
        "label": "Method 3: Sudden Spike (34→95°C)",
        "description": "DS18B20 suffers abrupt electrical spikes to 95°C -> triggers 'Abnormal Sensor Variation – Inspection Recommended'.",
    },
    {
        "id": "genuine_zero_ice_bath",
        "label": "Genuine 0°C Ice-Bath Probe",
        "description": "DS18B20 reads genuine 0.0°C while connected -> stored as valid 0.0 (not missing data).",
    },
]


class HardwareFaultSimulator:
    """
    Generates realistic 1 Hz multi-sensor frames matching the Arduino Uno serial schema
    and supports deterministic fault/hazard injection for verifying all project requirements.
    """

    def __init__(self) -> None:
        self.scenario: str = "normal"
        self.scenario_started_at: float = time.monotonic()
        self._boot_at: float = time.monotonic()
        self._seq: int = 0
        self._rng = random.Random(2026)

        self._fire_ds_temp: float = 29.0
        self._fire_dt_temp: float = 29.2
        self._fire_humidity: float = 54.0

    def set_scenario(self, scenario_id: str) -> Dict[str, Any]:
        valid_ids = {s["id"] for s in SCENARIO_CATALOG}
        if scenario_id not in valid_ids:
            raise ValueError(f"Unknown scenario '{scenario_id}'. Valid: {sorted(valid_ids)}")
        self.scenario = scenario_id
        self.scenario_started_at = time.monotonic()
        self._fire_ds_temp = 29.0
        self._fire_dt_temp = 29.2
        self._fire_humidity = 54.0
        return self.get_status()

    def get_status(self) -> Dict[str, Any]:
        meta = next((s for s in SCENARIO_CATALOG if s["id"] == self.scenario), SCENARIO_CATALOG[0])
        return {
            "active_scenario": self.scenario,
            "label": meta["label"],
            "description": meta["description"],
            "elapsed_sec": round(time.monotonic() - self.scenario_started_at, 1),
            "available_scenarios": SCENARIO_CATALOG,
        }

    def next_frame(self) -> Dict[str, Any] | None:
        """Returns None when simulating complete Arduino Uno USB disconnection."""
        if self.scenario == "arduino_disconnected":
            return None

        self._seq += 1
        now = time.monotonic()
        t_total = now - self._boot_at
        t_scen = now - self.scenario_started_at

        # Base ambient room waveform
        ambient = 28.6 + 1.1 * math.sin(t_total / 22.0)
        ds_ok = True
        dht_ok = True
        flame_ok = True

        ds_temp = round(ambient + self._rng.uniform(-0.18, 0.18), 2)
        dht_temp = round(ambient + 0.4 + self._rng.choice([-0.4, -0.2, 0.0, 0.2, 0.4]), 1)
        dht_hum = round(
            54.0 - 1.6 * math.sin(t_total / 25.0) + self._rng.choice([-0.8, -0.3, 0.0, 0.3, 0.8]),
            1,
        )
        flame_detected = 0
        flame_intensity = round(6.2 + self._rng.uniform(-1.1, 1.1), 1)

        if self.scenario == "sensor_disconnect_ds18b20":
            ds_ok = False
        elif self.scenario == "sensor_disconnect_dht11":
            dht_ok = False
        elif self.scenario == "stuck_ds18b20":
            # DS18B20 completely latched at 28.75°C while room/DHT11 drifts noticeably
            ds_temp = 28.75
            dht_temp = round(29.0 + 3.0 * min(1.0, t_scen / 14.0) + self._rng.uniform(-0.2, 0.2), 1)
        elif self.scenario == "false_flame_fault":
            # Flame latched ON while both temperature sensors remain cool & stable
            flame_detected = 1
            flame_intensity = round(78.0 + self._rng.uniform(-2.0, 2.0), 1)
        elif self.scenario == "genuine_fire":
            # Corroborated fire: flame ON + progressive heat rise + falling humidity
            flame_detected = 1
            flame_intensity = round(min(94.0, 68.0 + t_scen * 1.8 + self._rng.uniform(-1.5, 1.5)), 1)
            self._fire_ds_temp = min(58.0, self._fire_ds_temp + self._rng.uniform(1.4, 2.4))
            self._fire_dt_temp = min(53.0, self._fire_dt_temp + self._rng.uniform(1.1, 2.0))
            self._fire_humidity = max(24.0, self._fire_humidity - self._rng.uniform(0.9, 1.8))
            ds_temp = round(self._fire_ds_temp, 2)
            dht_temp = round(self._fire_dt_temp, 1)
            dht_hum = round(self._fire_humidity, 1)
        elif self.scenario == "sudden_spike_ds18b20":
            # Every ~4 cycles jump abruptly from ~31-34°C to 95.0°C (electrical fault / spike)
            if int(t_scen) % 4 in (1, 2):
                ds_temp = 95.0
            else:
                ds_temp = round(33.5 + self._rng.uniform(-0.4, 0.4), 2)
        elif self.scenario == "genuine_zero_ice_bath":
            # DS18B20 probe immersed in 0°C ice bath (occasionally 0.00°C, tiny ADC jitter 0.00..0.06°C)
            ds_temp = 0.0 if (self._seq % 2 == 0) else 0.06

        analog_raw = int(round(1023.0 * (1.0 - flame_intensity / 100.0)))

        return {
            "seq": self._seq,
            "uptime_ms": int(t_total * 1000),
            "port": "sim://arduino_uno_r3",
            "ds18b20": {"ok": ds_ok, "temp_c": ds_temp if ds_ok else None},
            "dht11": {
                "ok": dht_ok,
                "temp_c": dht_temp if dht_ok else None,
                "humidity_pct": dht_hum if dht_ok else None,
            },
            "flame": {
                "ok": flame_ok,
                "detected": flame_detected if flame_ok else None,
                "analog_raw": analog_raw if flame_ok else None,
                "intensity_pct": flame_intensity if flame_ok else None,
            },
        }
