import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from backend.models import AlertEvent


class SensorDatabase:
    """
    Thread-safe persistent SQLite database using WAL mode.
    Explicitly separates missing/disconnected data (SQL NULL + *_valid = 0)
    from genuine zero measurements (0.0 + *_valid = 1).
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=15.0)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensor_readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_ms INTEGER NOT NULL,
                    timestamp_iso TEXT NOT NULL,
                    arduino_connected INTEGER NOT NULL,

                    ds18b20_temp REAL NULL,
                    ds18b20_valid INTEGER NOT NULL,
                    ds18b20_conn_status TEXT NOT NULL,
                    ds18b20_health_status TEXT NOT NULL,

                    dht11_temp REAL NULL,
                    dht11_temp_valid INTEGER NOT NULL,
                    dht11_temp_conn_status TEXT NOT NULL,
                    dht11_temp_health_status TEXT NOT NULL,

                    dht11_humidity REAL NULL,
                    dht11_humidity_valid INTEGER NOT NULL,
                    dht11_humidity_conn_status TEXT NOT NULL,
                    dht11_humidity_health_status TEXT NOT NULL,

                    flame_status INTEGER NULL,
                    flame_intensity_pct REAL NULL,
                    flame_valid INTEGER NOT NULL,
                    flame_conn_status TEXT NOT NULL,
                    flame_health_status TEXT NOT NULL,

                    environmental_status TEXT NOT NULL,
                    ml_system_anomaly_score REAL NOT NULL DEFAULT 0.0
                );
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_readings_ts ON sensor_readings(timestamp_ms);"
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_ms INTEGER NOT NULL,
                    timestamp_iso TEXT NOT NULL,
                    sensor_id TEXT NOT NULL,
                    sensor_name TEXT NOT NULL,
                    alert_domain TEXT NOT NULL,
                    alert_category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts_log(timestamp_ms DESC);"
            )
            self._conn.commit()

    def insert_reading(self, snapshot: Dict[str, Any]) -> int:
        sensors = snapshot["sensors"]
        ds = sensors["ds18b20_temp"]
        dt = sensors["dht11_temp"]
        dh = sensors["dht11_humidity"]
        fl = sensors["flame_sensor"]

        flame_raw = fl["raw_value"]
        flame_int = int(round(flame_raw)) if (fl["is_valid"] and flame_raw is not None) else None
        flame_intensity = fl.get("extra", {}).get("intensity_pct") if fl["is_valid"] else None

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO sensor_readings (
                    timestamp_ms, timestamp_iso, arduino_connected,
                    ds18b20_temp, ds18b20_valid, ds18b20_conn_status, ds18b20_health_status,
                    dht11_temp, dht11_temp_valid, dht11_temp_conn_status, dht11_temp_health_status,
                    dht11_humidity, dht11_humidity_valid, dht11_humidity_conn_status, dht11_humidity_health_status,
                    flame_status, flame_intensity_pct, flame_valid, flame_conn_status, flame_health_status,
                    environmental_status, ml_system_anomaly_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot["timestamp_ms"],
                    snapshot["timestamp_iso"],
                    1 if snapshot["arduino_connected"] else 0,
                    ds["raw_value"] if ds["is_valid"] else None,
                    1 if ds["is_valid"] else 0,
                    ds["connection_status"],
                    ds["health_status"],
                    dt["raw_value"] if dt["is_valid"] else None,
                    1 if dt["is_valid"] else 0,
                    dt["connection_status"],
                    dt["health_status"],
                    dh["raw_value"] if dh["is_valid"] else None,
                    1 if dh["is_valid"] else 0,
                    dh["connection_status"],
                    dh["health_status"],
                    flame_int,
                    flame_intensity,
                    1 if fl["is_valid"] else 0,
                    fl["connection_status"],
                    fl["health_status"],
                    snapshot["environmental"]["status"],
                    float(snapshot["ml_summary"]["system_anomaly_score"]),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def insert_alert(self, alert: AlertEvent) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO alerts_log (
                    timestamp_ms, timestamp_iso, sensor_id, sensor_name,
                    alert_domain, alert_category, severity, title, reason, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert.timestamp_ms,
                    alert.timestamp_iso,
                    alert.sensor_id,
                    alert.sensor_name,
                    alert.alert_domain,
                    alert.alert_category,
                    alert.severity,
                    alert.title,
                    alert.reason,
                    json.dumps(alert.details or {}),
                ),
            )
            self._conn.commit()
            row_id = int(cur.lastrowid)
            alert.id = row_id
            return row_id

    def query_history(
        self,
        start_ms: Optional[int] = None,
        end_ms: Optional[int] = None,
        limit: int = 500,
        max_points: int = 300,
        missing_only: bool = False,
    ) -> Dict[str, Any]:
        clauses: List[str] = []
        params: List[Any] = []

        if start_ms is not None:
            clauses.append("timestamp_ms >= ?")
            params.append(int(start_ms))
        if end_ms is not None:
            clauses.append("timestamp_ms <= ?")
            params.append(int(end_ms))
        if missing_only:
            clauses.append(
                "(arduino_connected = 0 OR ds18b20_valid = 0 OR dht11_temp_valid = 0 "
                "OR dht11_humidity_valid = 0 OR flame_valid = 0)"
            )

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        safe_limit = max(1, min(int(limit), 5000))

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"""
                SELECT * FROM (
                    SELECT * FROM sensor_readings
                    {where_sql}
                    ORDER BY timestamp_ms DESC
                    LIMIT ?
                ) sub
                ORDER BY timestamp_ms ASC
                """,
                (*params, safe_limit),
            )
            rows = [dict(r) for r in cur.fetchall()]

        total_fetched = len(rows)
        if total_fetched > max_points and max_points > 10:
            step = max(1, total_fetched // max_points)
            sampled = rows[::step]
            if sampled[-1]["id"] != rows[-1]["id"]:
                sampled.append(rows[-1])
        else:
            sampled = rows

        # Compute summary distinguishing missing data vs genuine zero readings
        missing_counts = {
            "ds18b20_temp": sum(1 for r in rows if r["ds18b20_valid"] == 0),
            "dht11_temp": sum(1 for r in rows if r["dht11_temp_valid"] == 0),
            "dht11_humidity": sum(1 for r in rows if r["dht11_humidity_valid"] == 0),
            "flame_sensor": sum(1 for r in rows if r["flame_valid"] == 0),
        }
        genuine_zero_counts = {
            "ds18b20_temp": sum(
                1
                for r in rows
                if r["ds18b20_valid"] == 1
                and r["ds18b20_temp"] is not None
                and abs(float(r["ds18b20_temp"])) < 1e-6
            ),
            "dht11_temp": sum(
                1
                for r in rows
                if r["dht11_temp_valid"] == 1
                and r["dht11_temp"] is not None
                and abs(float(r["dht11_temp"])) < 1e-6
            ),
            "dht11_humidity": sum(
                1
                for r in rows
                if r["dht11_humidity_valid"] == 1
                and r["dht11_humidity"] is not None
                and abs(float(r["dht11_humidity"])) < 1e-6
            ),
            "flame_sensor": sum(
                1 for r in rows if r["flame_valid"] == 1 and r["flame_status"] == 0
            ),
        }

        formatted_points = []
        for r in sampled:
            formatted_points.append(
                {
                    "id": r["id"],
                    "timestamp_ms": r["timestamp_ms"],
                    "timestamp_iso": r["timestamp_iso"],
                    "arduino_connected": bool(r["arduino_connected"]),
                    "environmental_status": r["environmental_status"],
                    "ml_system_anomaly_score": r["ml_system_anomaly_score"],
                    "ds18b20_temp": {
                        "raw_value": r["ds18b20_temp"],
                        "display_value": r["ds18b20_temp"] if r["ds18b20_valid"] else 0.0,
                        "is_valid": bool(r["ds18b20_valid"]),
                        "conn_status": r["ds18b20_conn_status"],
                        "health_status": r["ds18b20_health_status"],
                    },
                    "dht11_temp": {
                        "raw_value": r["dht11_temp"],
                        "display_value": r["dht11_temp"] if r["dht11_temp_valid"] else 0.0,
                        "is_valid": bool(r["dht11_temp_valid"]),
                        "conn_status": r["dht11_temp_conn_status"],
                        "health_status": r["dht11_temp_health_status"],
                    },
                    "dht11_humidity": {
                        "raw_value": r["dht11_humidity"],
                        "display_value": r["dht11_humidity"] if r["dht11_humidity_valid"] else 0.0,
                        "is_valid": bool(r["dht11_humidity_valid"]),
                        "conn_status": r["dht11_humidity_conn_status"],
                        "health_status": r["dht11_humidity_health_status"],
                    },
                    "flame_sensor": {
                        "raw_value": r["flame_status"],
                        "display_value": r["flame_status"] if r["flame_valid"] else 0,
                        "intensity_pct": r["flame_intensity_pct"],
                        "is_valid": bool(r["flame_valid"]),
                        "conn_status": r["flame_conn_status"],
                        "health_status": r["flame_health_status"],
                    },
                }
            )

        return {
            "total_matching_rows": total_fetched,
            "returned_points": len(formatted_points),
            "missing_counts": missing_counts,
            "genuine_zero_counts": genuine_zero_counts,
            "points": formatted_points,
        }

    def query_alerts(
        self,
        start_ms: Optional[int] = None,
        end_ms: Optional[int] = None,
        domain: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if start_ms is not None:
            clauses.append("timestamp_ms >= ?")
            params.append(int(start_ms))
        if end_ms is not None:
            clauses.append("timestamp_ms <= ?")
            params.append(int(end_ms))
        if domain:
            clauses.append("alert_domain = ?")
            params.append(domain)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        safe_limit = max(1, min(int(limit), 500))

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"""
                SELECT * FROM alerts_log
                {where_sql}
                ORDER BY timestamp_ms DESC, id DESC
                LIMIT ?
                """,
                (*params, safe_limit),
            )
            rows = cur.fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            try:
                item["details"] = json.loads(item.pop("details_json") or "{}")
            except Exception:
                item["details"] = {}
            out.append(item)
        return out

    def get_storage_stats(self) -> Dict[str, Any]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total_readings,
                    MIN(timestamp_iso) AS earliest_iso,
                    MAX(timestamp_iso) AS latest_iso,
                    SUM(CASE WHEN arduino_connected = 0 THEN 1 ELSE 0 END) AS arduino_dropouts,
                    SUM(CASE WHEN ds18b20_valid = 0 OR dht11_temp_valid = 0
                              OR dht11_humidity_valid = 0 OR flame_valid = 0 THEN 1 ELSE 0 END) AS missing_sensor_cycles
                FROM sensor_readings
                """
            )
            r_row = dict(cur.fetchone())

            cur.execute("SELECT COUNT(*) AS total_alerts FROM alerts_log")
            a_row = dict(cur.fetchone())

        return {
            "db_path": self.db_path,
            "total_readings": int(r_row["total_readings"] or 0),
            "earliest_iso": r_row["earliest_iso"],
            "latest_iso": r_row["latest_iso"],
            "arduino_dropouts": int(r_row["arduino_dropouts"] or 0),
            "missing_sensor_cycles": int(r_row["missing_sensor_cycles"] or 0),
            "total_alerts": int(a_row["total_alerts"] or 0),
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
