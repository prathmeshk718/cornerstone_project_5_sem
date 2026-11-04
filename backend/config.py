import json
from pathlib import Path
from typing import Any, Dict
from pydantic import BaseModel, Field

SENSOR_IDS = ("ds18b20_temp", "flame_sensor", "dht11_temp", "dht11_humidity")

SENSOR_CATALOG: Dict[str, Dict[str, Any]] = {
    "ds18b20_temp": {
        "id": "ds18b20_temp",
        "name": "DS18B20 Temperature",
        "hardware_module": "DS18B20 1-Wire Digital Temperature Probe",
        "measurement_type": "Probe / Surface Temperature",
        "unit": "°C",
        "arduino_pin": "Digital Pin D2 (OneWire, 4.7kΩ pull-up)",
        "ros2_topic": "/sensors/ds18b20/temperature",
        "ros2_msg_type": "sensor_msgs/msg/Temperature",
        "operating_range": "-55°C to +125°C (±0.5°C accuracy)",
        "resolution": "0.0625°C (12-bit ADC)",
        "is_digital_state": False,
        "chart_min_hint": 0.0,
        "chart_max_hint": 60.0,
    },
    "flame_sensor": {
        "id": "flame_sensor",
        "name": "Flame Sensor",
        "hardware_module": "IR Flame Detection Module (760nm–1100nm, LM393)",
        "measurement_type": "Flame Presence (Digital DO + Analog IR Intensity)",
        "unit": "State (0/1)",
        "arduino_pin": "Digital Pin D4 (DO, Active-LOW) + Analog Pin A0 (AO)",
        "ros2_topic": "/sensors/flame/detected & /sensors/flame/intensity",
        "ros2_msg_type": "std_msgs/msg/Bool & std_msgs/msg/Float32",
        "operating_range": "0 = No Flame, 1 = Flame Detected (IR 0–100%)",
        "resolution": "Binary Comparator + 10-bit ADC (0–1023)",
        "is_digital_state": True,
        "chart_min_hint": 0.0,
        "chart_max_hint": 1.0,
    },
    "dht11_temp": {
        "id": "dht11_temp",
        "name": "DHT11 Temperature",
        "hardware_module": "DHT11 Environmental Temperature & Humidity Sensor",
        "measurement_type": "Ambient Air Temperature (Independent of DS18B20)",
        "unit": "°C",
        "arduino_pin": "Digital Pin D3 (Single-Wire Digital Bus)",
        "ros2_topic": "/sensors/dht11/temperature",
        "ros2_msg_type": "sensor_msgs/msg/Temperature",
        "operating_range": "0°C to +50°C (±2.0°C accuracy)",
        "resolution": "0.1°C – 1.0°C",
        "is_digital_state": False,
        "chart_min_hint": 0.0,
        "chart_max_hint": 60.0,
    },
    "dht11_humidity": {
        "id": "dht11_humidity",
        "name": "DHT11 Humidity",
        "hardware_module": "DHT11 Environmental Temperature & Humidity Sensor",
        "measurement_type": "Ambient Relative Humidity",
        "unit": "%",
        "arduino_pin": "Digital Pin D3 (Single-Wire Digital Bus)",
        "ros2_topic": "/sensors/dht11/humidity",
        "ros2_msg_type": "sensor_msgs/msg/RelativeHumidity",
        "operating_range": "20% to 90% RH (±5% RH accuracy)",
        "resolution": "1% RH",
        "is_digital_state": False,
        "chart_min_hint": 0.0,
        "chart_max_hint": 100.0,
    },
}


class ThresholdsConfig(BaseModel):
    """Configurable thresholds for timeouts, sensor-health analytics, Cross-Validation, and ML."""

    # Connectivity & Warmup
    arduino_timeout_sec: float = Field(default=3.5, ge=1.0, le=60.0)
    sensor_timeout_sec: float = Field(default=3.5, ge=1.0, le=60.0)
    min_samples_for_health: int = Field(default=5, ge=3, le=50)

    # Method 1: Constant Value (Stuck Sensor) Detection
    ds18b20_stuck_warn_sec: float = Field(default=20.0, ge=5.0, le=600.0)
    ds18b20_stuck_maint_sec: float = Field(default=40.0, ge=10.0, le=1200.0)
    ds18b20_variation_eps: float = Field(default=0.02, ge=0.0, le=1.0)

    dht11_temp_stuck_warn_sec: float = Field(default=35.0, ge=5.0, le=600.0)
    dht11_temp_stuck_maint_sec: float = Field(default=70.0, ge=10.0, le=1200.0)
    dht11_temp_variation_eps: float = Field(default=0.05, ge=0.0, le=1.0)

    dht11_hum_stuck_warn_sec: float = Field(default=35.0, ge=5.0, le=600.0)
    dht11_hum_stuck_maint_sec: float = Field(default=70.0, ge=10.0, le=1200.0)
    dht11_hum_variation_eps: float = Field(default=0.05, ge=0.0, le=2.0)

    # Digital flame sensor: OFF (0) is normal in safe environments; check analog freeze or uncorroborated ON
    flame_analog_stuck_sec: float = Field(default=45.0, ge=10.0, le=1200.0)

    # Method 2: Multi-Sensor Correlation & Cross-Validation
    flame_validation_window_sec: float = Field(default=4.0, ge=1.0, le=60.0)
    flame_fault_maint_sec: float = Field(default=10.0, ge=3.0, le=120.0)
    fire_temp_rise_threshold_c: float = Field(default=2.0, ge=0.5, le=25.0)
    fire_abs_temp_threshold_c: float = Field(default=42.0, ge=30.0, le=100.0)
    fire_humidity_drop_hint_pct: float = Field(default=3.0, ge=0.5, le=40.0)
    baseline_window_samples: int = Field(default=20, ge=5, le=120)

    max_temp_sensor_divergence_c: float = Field(default=6.0, ge=2.0, le=30.0)
    temp_divergence_persist_sec: float = Field(default=6.0, ge=2.0, le=120.0)

    # Method 3: Sudden Change & Abnormal Variation Detection
    ds18b20_max_step_c: float = Field(default=6.0, ge=1.0, le=50.0)
    dht11_temp_max_step_c: float = Field(default=6.0, ge=1.0, le=50.0)
    dht11_hum_max_step_pct: float = Field(default=15.0, ge=2.0, le=60.0)
    variation_window_samples: int = Field(default=12, ge=5, le=60)
    abnormal_spike_hold_sec: float = Field(default=8.0, ge=2.0, le=60.0)
    spikes_for_maintenance: int = Field(default=3, ge=2, le=20)

    # Hybrid ML Condition Scoring
    ml_anomaly_warn_threshold: float = Field(default=0.68, ge=0.3, le=0.98)
    ml_anomaly_maint_threshold: float = Field(default=0.85, ge=0.5, le=0.99)

    # Alert Cooldown (prevents duplicate alert flooding in DB while issue persists)
    alert_cooldown_sec: float = Field(default=15.0, ge=3.0, le=300.0)


def load_thresholds(path: str) -> ThresholdsConfig:
    p = Path(path)
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            return ThresholdsConfig(**raw)
        except Exception:
            pass
    cfg = ThresholdsConfig()
    save_thresholds(path, cfg)
    return cfg


def save_thresholds(path: str, config: ThresholdsConfig) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(config.model_dump(), indent=2), encoding="utf-8")
