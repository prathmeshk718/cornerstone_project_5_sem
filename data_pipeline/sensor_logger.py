import csv
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional


CSV_COLUMNS = [
    "timestamp_unix",
    "iso_timestamp",
    "seq",
    "source_mode",
    "ds18b20_temp_c",
    "dht11_temp_c",
    "dht11_humidity_pct",
    "yg1006_flame",
    "yg1006_ir_raw",
    "smoke_raw_adc",
    "temp_differential_c",
    "operator_label",
    "operator_label_name",
    "ml_predicted_class",
    "ml_predicted_label",
    "anomaly_detected",
    "risk_level",
]

LABEL_MAP = {
    0: "Normal Operating Condition",
    1: "Equipment Overheating (DS18B20)",
    2: "Environmental Temp/Humidity Anomaly (DHT11)",
    3: "Smoke / Gas Emission Anomaly",
    4: "Fire / Flame Hazard (YG1006)",
}


class SensorDataLogger:
    """
    Structured Data Acquisition and Historical Logging Engine.
    Maintains strict separation between independent sensor measurements:
      - DS18B20: Equipment Temperature (°C)
      - DHT11:   Environmental Temperature (°C) and Relative Humidity (%)
      - YG1006:  Infrared/Flame Detection Status (0/1) & Raw IR
      - Smoke:   Raw Analog Sensor Reading (0-1023 ADC)
    Writes synchronized records to both CSV files (for ML dataset building)
    and an SQLite database (for fast historical querying).
    """

    def __init__(
        self,
        csv_path: str = "data/sensor_readings_history.csv",
        sqlite_path: str = "data/condition_monitoring.db",
        session_dir: str = "data/logged_sessions",
    ):
        self.csv_path = csv_path
        self.sqlite_path = sqlite_path
        self.session_dir = session_dir
        self._lock = threading.Lock()

        os.makedirs(os.path.dirname(self.csv_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.sqlite_path), exist_ok=True)
        os.makedirs(self.session_dir, exist_ok=True)

        self.is_recording: bool = True
        self.current_operator_label: int = 0
        self.current_session_id: str = datetime.now().strftime("session_%Y%m%d_%H%M%S")
        self.current_session_csv: str = os.path.join(
            self.session_dir, f"{self.current_session_id}.csv"
        )
        self.session_row_count: int = 0
        self.total_row_count: int = 0

        self._init_csv(self.csv_path)
        self._init_csv(self.current_session_csv)
        self._init_sqlite()
        self.total_row_count = self._count_csv_rows(self.csv_path)

    def _init_csv(self, path: str) -> None:
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                writer.writeheader()

    def _count_csv_rows(self, path: str) -> int:
        if not os.path.exists(path):
            return 0
        with open(path, mode="r", encoding="utf-8") as f:
            return max(0, sum(1 for _ in f) - 1)

    def _init_sqlite(self) -> None:
        with sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sensor_readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_unix REAL NOT NULL,
                    iso_timestamp TEXT NOT NULL,
                    seq INTEGER,
                    source_mode TEXT,
                    ds18b20_temp_c REAL NOT NULL,
                    dht11_temp_c REAL NOT NULL,
                    dht11_humidity_pct REAL NOT NULL,
                    yg1006_flame INTEGER NOT NULL,
                    yg1006_ir_raw INTEGER,
                    smoke_raw_adc INTEGER NOT NULL,
                    temp_differential_c REAL,
                    operator_label INTEGER DEFAULT 0,
                    operator_label_name TEXT,
                    ml_predicted_class INTEGER,
                    ml_predicted_label TEXT,
                    anomaly_detected INTEGER,
                    risk_level TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ts ON sensor_readings(timestamp_unix)"
            )
            conn.commit()

    def set_recording(self, active: bool) -> Dict[str, Any]:
        with self._lock:
            self.is_recording = bool(active)
            return self.get_status()

    def set_operator_label(self, label_id: int) -> Dict[str, Any]:
        with self._lock:
            if label_id in LABEL_MAP:
                self.current_operator_label = label_id
            return self.get_status()

    def start_new_session(self, reset_main_csv: bool = False) -> Dict[str, Any]:
        with self._lock:
            self.current_session_id = datetime.now().strftime(
                "session_%Y%m%d_%H%M%S"
            )
            self.current_session_csv = os.path.join(
                self.session_dir, f"{self.current_session_id}.csv"
            )
            self._init_csv(self.current_session_csv)
            self.session_row_count = 0

            if reset_main_csv:
                if os.path.exists(self.csv_path):
                    os.remove(self.csv_path)
                self._init_csv(self.csv_path)
                with sqlite3.connect(self.sqlite_path) as conn:
                    conn.execute("DELETE FROM sensor_readings")
                    conn.commit()
                self.total_row_count = 0

            return self.get_status()

    def log_reading(
        self,
        raw_frame: Dict[str, Any],
        analytics_result: Optional[Dict[str, Any]] = None,
        override_label: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Structures and logs a single multi-sensor reading to CSV and SQLite.
        """
        with self._lock:
            ts_unix = float(raw_frame.get("timestamp_unix", time.time()))
            iso_ts = datetime.fromtimestamp(ts_unix, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            ds_temp = round(float(raw_frame.get("ds18b20_temp_c", 0.0)), 2)
            dht_temp = round(float(raw_frame.get("dht11_temp_c", 0.0)), 2)
            dht_hum = round(float(raw_frame.get("dht11_humidity_pct", 0.0)), 1)
            yg_flame = int(raw_frame.get("yg1006_flame", 0))
            yg_ir = int(raw_frame.get("yg1006_ir_raw", 950 if yg_flame == 0 else 180))
            smoke_adc = int(raw_frame.get("smoke_raw_adc", 0))
            temp_diff = round(ds_temp - dht_temp, 2)

            label_id = (
                override_label
                if override_label is not None
                else self.current_operator_label
            )
            label_name = LABEL_MAP.get(label_id, "Unknown")

            ml_class = (
                int(analytics_result.get("predicted_class_id", 0))
                if analytics_result
                else label_id
            )
            ml_label = (
                str(analytics_result.get("predicted_label", label_name))
                if analytics_result
                else label_name
            )
            anomaly_flag = (
                1
                if (analytics_result and analytics_result.get("is_anomaly", False))
                else 0
            )
            risk_level = (
                str(analytics_result.get("risk_level", "LOW"))
                if analytics_result
                else "LOW"
            )

            record = {
                "timestamp_unix": round(ts_unix, 3),
                "iso_timestamp": iso_ts,
                "seq": int(raw_frame.get("seq", 0)),
                "source_mode": str(raw_frame.get("source_mode", "serial")),
                "ds18b20_temp_c": ds_temp,
                "dht11_temp_c": dht_temp,
                "dht11_humidity_pct": dht_hum,
                "yg1006_flame": yg_flame,
                "yg1006_ir_raw": yg_ir,
                "smoke_raw_adc": smoke_adc,
                "temp_differential_c": temp_diff,
                "operator_label": label_id,
                "operator_label_name": label_name,
                "ml_predicted_class": ml_class,
                "ml_predicted_label": ml_label,
                "anomaly_detected": anomaly_flag,
                "risk_level": risk_level,
            }

            if not self.is_recording:
                return record

            # Append to main historical CSV
            with open(self.csv_path, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                writer.writerow(record)

            # Append to active session CSV
            with open(
                self.current_session_csv, mode="a", newline="", encoding="utf-8"
            ) as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                writer.writerow(record)

            # Insert into SQLite database
            with sqlite3.connect(self.sqlite_path) as conn:
                conn.execute(
                    """
                    INSERT INTO sensor_readings (
                        timestamp_unix, iso_timestamp, seq, source_mode,
                        ds18b20_temp_c, dht11_temp_c, dht11_humidity_pct,
                        yg1006_flame, yg1006_ir_raw, smoke_raw_adc,
                        temp_differential_c, operator_label, operator_label_name,
                        ml_predicted_class, ml_predicted_label, anomaly_detected, risk_level
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(record[col] for col in CSV_COLUMNS),
                )
                conn.commit()

            self.session_row_count += 1
            self.total_row_count += 1
            return record

    def get_recent_history(self, limit: int = 120) -> List[Dict[str, Any]]:
        with self._lock:
            with sqlite3.connect(self.sqlite_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    """
                    SELECT * FROM sensor_readings
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                rows = [dict(r) for r in cur.fetchall()]
                rows.reverse()
                return rows

    def get_historical_summary(self) -> Dict[str, Any]:
        with self._lock:
            with sqlite3.connect(self.sqlite_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    """
                    SELECT
                        COUNT(*) as total_samples,
                        ROUND(AVG(ds18b20_temp_c), 2) as avg_ds18b20_temp,
                        ROUND(MIN(ds18b20_temp_c), 2) as min_ds18b20_temp,
                        ROUND(MAX(ds18b20_temp_c), 2) as max_ds18b20_temp,
                        ROUND(AVG(dht11_temp_c), 2) as avg_dht11_temp,
                        ROUND(MAX(dht11_temp_c), 2) as max_dht11_temp,
                        ROUND(AVG(dht11_humidity_pct), 1) as avg_dht11_humidity,
                        ROUND(MAX(dht11_humidity_pct), 1) as max_dht11_humidity,
                        ROUND(AVG(smoke_raw_adc), 1) as avg_smoke_adc,
                        MAX(smoke_raw_adc) as max_smoke_adc,
                        SUM(CASE WHEN yg1006_flame = 1 THEN 1 ELSE 0 END) as flame_events,
                        SUM(CASE WHEN anomaly_detected = 1 THEN 1 ELSE 0 END) as anomaly_events
                    FROM sensor_readings
                    """
                )
                summary = dict(cur.fetchone() or {})

                label_cur = conn.execute(
                    """
                    SELECT operator_label, operator_label_name, COUNT(*) as count
                    FROM sensor_readings
                    GROUP BY operator_label, operator_label_name
                    ORDER BY operator_label ASC
                    """
                )
                summary["class_distribution"] = [
                    dict(r) for r in label_cur.fetchall()
                ]
                return summary

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_recording": self.is_recording,
            "current_operator_label": self.current_operator_label,
            "current_operator_label_name": LABEL_MAP.get(
                self.current_operator_label, "Normal Operating Condition"
            ),
            "available_labels": [
                {"id": k, "name": v} for k, v in LABEL_MAP.items()
            ],
            "current_session_id": self.current_session_id,
            "current_session_csv": self.current_session_csv,
            "main_csv_path": self.csv_path,
            "sqlite_db_path": self.sqlite_path,
            "session_row_count": self.session_row_count,
            "total_row_count": self.total_row_count,
        }
