import json
import math
import random
import time
from typing import Dict, Any, List, Optional

import serial
import serial.tools.list_ports


SCENARIOS = {
    "normal": {
        "name": "Normal Operating Condition",
        "description": "Equipment temperature stable (~42°C), normal ambient temp (~26°C) & humidity (~48%), no flame, clean air smoke ADC (~155).",
        "default_label": 0,
    },
    "equipment_overheat": {
        "name": "Equipment Overheating (DS18B20 Fault)",
        "description": "DS18B20 equipment temperature rises to 66–76°C while DHT11 ambient temperature remains normal (~27°C).",
        "default_label": 1,
    },
    "ambient_stress": {
        "name": "Environmental Temp & Humidity Stress (DHT11)",
        "description": "Surrounding room temperature (39°C) and relative humidity (81%) exceed safe environmental limits.",
        "default_label": 2,
    },
    "smoke_anomaly": {
        "name": "Smoke / Gas Emission Anomaly",
        "description": "Analog smoke sensor ADC climbs to 460–620 indicating smoke or gas accumulation near equipment.",
        "default_label": 3,
    },
    "flame_hazard": {
        "name": "Critical Infrared Flame Detection (YG1006)",
        "description": "YG1006 NPN phototransistor detects IR flame signature (status = 1) with elevated smoke and thermal readings.",
        "default_label": 4,
    },
    "gradual_degradation": {
        "name": "Predictive Maintenance Drift (Gradual Bearing/Coil Heating)",
        "description": "Slow, steady upward drift in DS18B20 temperature (+0.35°C/step) and smoke ADC to trigger early predictive maintenance warnings before failure.",
        "default_label": 1,
    },
}


class SensorDataSource:
    """
    Manages acquisition from either:
      1. Physical Arduino Uno over USB Serial (pyserial -> ROS 2 Humble pipeline)
      2. Hardware-accurate Multi-Sensor Simulator (when testing without physical Arduino)
    """

    def __init__(
        self,
        serial_port: str = "/dev/ttyACM0",
        baud_rate: int = 115200,
        prefer_serial: bool = False,
    ):
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.mode = "serial" if prefer_serial else "simulator"
        self.scenario = "normal"
        self.seq = 0
        self._serial_conn: Optional[serial.Serial] = None
        self.serial_connected = False
        self.last_error: Optional[str] = None

        # Internal state for smooth physical simulation
        self._sim_ds18b20 = 42.2
        self._sim_dht_temp = 26.4
        self._sim_dht_hum = 48.5
        self._sim_smoke_adc = 158.0
        self._sim_drift_step = 0

        if prefer_serial:
            self._try_connect_serial()

    @staticmethod
    def list_available_serial_ports() -> List[Dict[str, str]]:
        ports = []
        for p in serial.tools.list_ports.comports():
            ports.append(
                {
                    "device": p.device,
                    "description": p.description or "Serial Device",
                    "hwid": p.hwid or "",
                }
            )
        return ports

    def _try_connect_serial(self) -> bool:
        try:
            if self._serial_conn and self._serial_conn.is_open:
                self._serial_conn.close()
            self._serial_conn = serial.Serial(
                port=self.serial_port, baudrate=self.baud_rate, timeout=1.2
            )
            self.serial_connected = True
            self.last_error = None
            return True
        except Exception as exc:
            self.serial_connected = False
            self.last_error = f"Serial port {self.serial_port} unavailable: {exc}"
            return False

    def configure(
        self,
        mode: Optional[str] = None,
        scenario: Optional[str] = None,
        serial_port: Optional[str] = None,
        baud_rate: Optional[int] = None,
    ) -> Dict[str, Any]:
        if serial_port:
            self.serial_port = serial_port
        if baud_rate:
            self.baud_rate = int(baud_rate)
        if scenario and scenario in SCENARIOS:
            if scenario != self.scenario:
                self._sim_drift_step = 0
            self.scenario = scenario

        if mode in ("serial", "simulator"):
            self.mode = mode
            if self.mode == "serial":
                ok = self._try_connect_serial()
                if not ok:
                    # Keep mode flag informative, fallback gracefully if requested
                    pass
            else:
                if self._serial_conn and self._serial_conn.is_open:
                    self._serial_conn.close()
                self.serial_connected = False

        return self.get_status()

    def read_next_frame(self) -> Dict[str, Any]:
        self.seq += 1
        now = time.time()

        if self.mode == "serial":
            if not self.serial_connected:
                self._try_connect_serial()
            if self.serial_connected and self._serial_conn:
                try:
                    raw_line = (
                        self._serial_conn.readline()
                        .decode("utf-8", errors="ignore")
                        .strip()
                    )
                    if raw_line and not raw_line.startswith("#"):
                        if raw_line.startswith("{"):
                            payload = json.loads(raw_line)
                            return {
                                "seq": int(payload.get("seq", self.seq)),
                                "timestamp_unix": now,
                                "source_mode": "arduino_serial_ros2",
                                "ds18b20_temp_c": float(
                                    payload.get("ds18b20_temp_c", 0.0)
                                ),
                                "dht11_temp_c": float(
                                    payload.get("dht11_temp_c", 0.0)
                                ),
                                "dht11_humidity_pct": float(
                                    payload.get("dht11_humidity_pct", 0.0)
                                ),
                                "yg1006_flame": int(payload.get("yg1006_flame", 0)),
                                "yg1006_ir_raw": int(
                                    payload.get("yg1006_ir_raw", 960)
                                ),
                                "smoke_raw_adc": int(
                                    payload.get("smoke_raw_adc", 0)
                                ),
                                "connection_ok": True,
                            }
                        else:
                            # Support comma-separated fallback: ds18b20,dht_t,dht_h,flame,smoke
                            parts = [p.strip() for p in raw_line.split(",")]
                            if len(parts) >= 5:
                                return {
                                    "seq": self.seq,
                                    "timestamp_unix": now,
                                    "source_mode": "arduino_serial_ros2",
                                    "ds18b20_temp_c": float(parts[0]),
                                    "dht11_temp_c": float(parts[1]),
                                    "dht11_humidity_pct": float(parts[2]),
                                    "yg1006_flame": int(float(parts[3])),
                                    "yg1006_ir_raw": 200
                                    if int(float(parts[3])) == 1
                                    else 960,
                                    "smoke_raw_adc": int(float(parts[4])),
                                    "connection_ok": True,
                                }
                except Exception as exc:
                    self.serial_connected = False
                    self.last_error = f"Serial read error: {exc}"

        # Hardware Simulator Mode (or Serial Fallback with clear status)
        return self._generate_simulated_frame(now)

    def _generate_simulated_frame(self, now: float) -> Dict[str, Any]:
        yg_flame = 0
        yg_ir_raw = int(955 + random.uniform(-15, 15))

        if self.scenario == "normal":
            target_ds = 42.5 + 1.2 * math.sin(self.seq * 0.08)
            target_dht_t = 26.5 + 0.5 * math.sin(self.seq * 0.05)
            target_dht_h = 48.0 + 1.8 * math.cos(self.seq * 0.06)
            target_smoke = 158.0 + 10.0 * math.sin(self.seq * 0.09)
        elif self.scenario == "equipment_overheat":
            target_ds = 72.5 + 2.5 * math.sin(self.seq * 0.12)
            target_dht_t = 27.2 + 0.6 * math.sin(self.seq * 0.05)
            target_dht_h = 46.5 + 1.5 * math.cos(self.seq * 0.06)
            target_smoke = 205.0 + 14.0 * math.sin(self.seq * 0.1)
        elif self.scenario == "ambient_stress":
            target_ds = 49.0 + 1.0 * math.sin(self.seq * 0.08)
            target_dht_t = 39.5 + 1.2 * math.sin(self.seq * 0.07)
            target_dht_h = 81.0 + 2.5 * math.cos(self.seq * 0.08)
            target_smoke = 172.0 + 10.0 * math.sin(self.seq * 0.09)
        elif self.scenario == "smoke_anomaly":
            target_ds = 47.5 + 1.4 * math.sin(self.seq * 0.09)
            target_dht_t = 27.8 + 0.6 * math.sin(self.seq * 0.05)
            target_dht_h = 51.0 + 1.5 * math.cos(self.seq * 0.06)
            target_smoke = 540.0 + 45.0 * math.sin(self.seq * 0.15)
        elif self.scenario == "flame_hazard":
            target_ds = 69.0 + 3.0 * math.sin(self.seq * 0.15)
            target_dht_t = 34.5 + 1.5 * math.sin(self.seq * 0.1)
            target_dht_h = 36.0 + 2.0 * math.cos(self.seq * 0.1)
            target_smoke = 685.0 + 50.0 * math.sin(self.seq * 0.2)
            yg_flame = 1
            yg_ir_raw = int(145 + random.uniform(-25, 25))
        elif self.scenario == "gradual_degradation":
            self._sim_drift_step = min(self._sim_drift_step + 1, 80)
            target_ds = 43.0 + (self._sim_drift_step * 0.36)
            target_dht_t = 26.8 + (self._sim_drift_step * 0.03)
            target_dht_h = 49.0 + 1.2 * math.cos(self.seq * 0.06)
            target_smoke = 162.0 + (self._sim_drift_step * 2.4)
        else:
            target_ds = 42.5
            target_dht_t = 26.5
            target_dht_h = 48.0
            target_smoke = 158.0

        # First-order thermal & gas inertia for realistic sensor dynamics
        alpha = 0.32
        self._sim_ds18b20 = (
            (1 - alpha) * self._sim_ds18b20
            + alpha * target_ds
            + random.gauss(0, 0.22)
        )
        self._sim_dht_temp = (
            (1 - alpha) * self._sim_dht_temp
            + alpha * target_dht_t
            + random.gauss(0, 0.15)
        )
        self._sim_dht_hum = (
            (1 - alpha) * self._sim_dht_hum
            + alpha * target_dht_h
            + random.gauss(0, 0.35)
        )
        self._sim_smoke_adc = (
            (1 - alpha) * self._sim_smoke_adc
            + alpha * target_smoke
            + random.gauss(0, 4.0)
        )

        return {
            "seq": self.seq,
            "timestamp_unix": now,
            "source_mode": f"ros2_sim:{self.scenario}",
            "ds18b20_temp_c": round(self._sim_ds18b20, 2),
            "dht11_temp_c": round(self._sim_dht_temp, 2),
            "dht11_humidity_pct": round(
                max(10.0, min(99.0, self._sim_dht_hum)), 1
            ),
            "yg1006_flame": yg_flame,
            "yg1006_ir_raw": yg_ir_raw,
            "smoke_raw_adc": int(max(0, min(1023, round(self._sim_smoke_adc)))),
            "connection_ok": True,
        }

    def get_status(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "scenario": self.scenario,
            "scenario_info": SCENARIOS.get(self.scenario, {}),
            "available_scenarios": [
                {"id": k, **v} for k, v in SCENARIOS.items()
            ],
            "serial_port": self.serial_port,
            "baud_rate": self.baud_rate,
            "serial_connected": self.serial_connected,
            "available_ports": self.list_available_serial_ports(),
            "last_error": self.last_error,
        }


def seed_initial_historical_dataset(logger, num_samples: int = 300) -> int:
    """
    Populates an initial baseline CSV & SQLite dataset if empty so that:
      1. Historical data structure is immediately visible in CSV/SQLite.
      2. The ML Framework can train and compute full evaluation metrics
         (Accuracy, Precision, Recall, F1, Confusion Matrix) right away.
      3. Users can clear/reset or append real Arduino hardware data at any time.
    """
    if logger.total_row_count >= 50:
        return logger.total_row_count

    rng = random.Random(42)
    base_ts = time.time() - (num_samples * 2.0)

    # Distribution: 55% Normal, 15% Equip Overheat, 12% Ambient Stress, 10% Smoke, 8% Flame
    regimes = (
        [(0, "normal")] * int(num_samples * 0.55)
        + [(1, "equipment_overheat")] * int(num_samples * 0.15)
        + [(2, "ambient_stress")] * int(num_samples * 0.12)
        + [(3, "smoke_anomaly")] * int(num_samples * 0.10)
        + [(4, "flame_hazard")] * (
            num_samples
            - int(num_samples * 0.55)
            - int(num_samples * 0.15)
            - int(num_samples * 0.12)
            - int(num_samples * 0.10)
        )
    )
    rng.shuffle(regimes)

    for idx, (label_id, regime_name) in enumerate(regimes, start=1):
        ts = base_ts + idx * 2.0
        if label_id == 0:  # Normal
            ds_t = rng.gauss(42.5, 2.8)
            dht_t = rng.gauss(26.2, 1.8)
            dht_h = rng.gauss(48.5, 5.0)
            flame = 0
            ir_raw = int(rng.gauss(955, 20))
            smoke = int(rng.gauss(160, 22))
            risk = "LOW"
            anom = False
        elif label_id == 1:  # Equipment Overheating (DS18B20)
            ds_t = rng.gauss(68.5, 5.5)
            dht_t = rng.gauss(27.5, 2.0)
            dht_h = rng.gauss(47.0, 5.0)
            flame = 0
            ir_raw = int(rng.gauss(930, 25))
            smoke = int(rng.gauss(210, 30))
            risk = "HIGH"
            anom = True
        elif label_id == 2:  # Environmental Stress (DHT11)
            ds_t = rng.gauss(48.0, 3.0)
            dht_t = rng.gauss(39.0, 2.5)
            dht_h = rng.gauss(80.5, 4.5)
            flame = 0
            ir_raw = int(rng.gauss(950, 20))
            smoke = int(rng.gauss(175, 25))
            risk = "MODERATE"
            anom = True
        elif label_id == 3:  # Smoke / Gas Emission
            ds_t = rng.gauss(47.5, 3.5)
            dht_t = rng.gauss(28.0, 2.0)
            dht_h = rng.gauss(52.0, 5.0)
            flame = 0
            ir_raw = int(rng.gauss(920, 30))
            smoke = int(rng.gauss(510, 65))
            risk = "HIGH"
            anom = True
        else:  # Flame Hazard (YG1006)
            ds_t = rng.gauss(66.0, 6.0)
            dht_t = rng.gauss(34.0, 3.0)
            dht_h = rng.gauss(37.0, 5.0)
            flame = 1 if rng.random() > 0.06 else 0
            ir_raw = int(rng.gauss(160, 35))
            smoke = int(rng.gauss(640, 80))
            risk = "CRITICAL"
            anom = True

        # Simulate realistic transition boundary states (~5% of samples)
        if rng.random() < 0.05:
            ds_t = rng.uniform(51.0, 58.0)
            dht_t = rng.uniform(31.0, 36.5)
            dht_h = rng.uniform(64.0, 74.0)
            smoke = int(rng.uniform(260, 350))

        frame = {
            "seq": idx,
            "timestamp_unix": ts,
            "source_mode": f"baseline_log:{regime_name}",
            "ds18b20_temp_c": round(ds_t, 2),
            "dht11_temp_c": round(dht_t, 2),
            "dht11_humidity_pct": round(max(15.0, min(98.0, dht_h)), 1),
            "yg1006_flame": flame,
            "yg1006_ir_raw": max(0, min(1023, ir_raw)),
            "smoke_raw_adc": max(0, min(1023, smoke)),
        }
        analytics_stub = {
            "predicted_class_id": label_id,
            "predicted_label": logger.get_status()["available_labels"][label_id][
                "name"
            ],
            "is_anomaly": anom,
            "risk_level": risk,
        }
        logger.log_reading(
            frame, analytics_result=analytics_stub, override_label=label_id
        )

    return logger.total_row_count
