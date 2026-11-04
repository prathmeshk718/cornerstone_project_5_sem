import asyncio
from collections import deque
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Set

from backend.analytics.engine import ConditionMonitoringEngine
from backend.config import (
    SENSOR_CATALOG,
    SENSOR_IDS,
    ThresholdsConfig,
    load_thresholds,
    save_thresholds,
)
from backend.ingestion.serial_reader import DirectSerialReader
from backend.ingestion.simulator import HardwareFaultSimulator
from backend.storage.database import SensorDatabase


class MonitoringPipeline:
    """
    Continuous background pipeline:
      Ingestion (ROS 2 Humble / USB Serial / Simulator)
        -> Heartbeat & Per-Sensor Timeout Supervisor
        -> Condition Monitoring + Cross-Validation + scikit-learn ML
        -> Continuous SQLite Persistence
        -> WebSocket Broadcast to React Dashboard
    """

    def __init__(
        self,
        mode: str = "simulator",
        db_path: str = "/home/user/backend/data/sensor_monitoring.db",
        thresholds_path: str = "/home/user/backend/data/thresholds.json",
        serial_port: str = "/dev/ttyACM0",
    ) -> None:
        self.mode = mode.lower()  # "ros2" | "serial" | "simulator"
        self.thresholds_path = thresholds_path
        self.thresholds: ThresholdsConfig = load_thresholds(thresholds_path)
        self.db = SensorDatabase(db_path)
        self.engine = ConditionMonitoringEngine(self.thresholds)
        self.simulator = HardwareFaultSimulator()

        self._lock = threading.Lock()
        self._last_heartbeat_mono: Optional[float] = None
        self._arduino_forced_off: bool = False
        self._serial_port_label: str = serial_port
        self._frame_seq: Optional[int] = None

        self._sensor_latest_val: Dict[str, Optional[float]] = {sid: None for sid in SENSOR_IDS}
        self._sensor_latest_mono: Dict[str, Optional[float]] = {sid: None for sid in SENSOR_IDS}
        self._sensor_explicit_ok: Dict[str, bool] = {sid: False for sid in SENSOR_IDS}
        self._flame_extra: Dict[str, Any] = {"intensity_pct": 0.0, "analog_raw": None}

        self._latest_snapshot: Optional[Dict[str, Any]] = None
        self._recent_ring: Deque[Dict[str, Any]] = deque(maxlen=90)
        self._ros2_hook: Optional[Callable[[Dict[str, Any], List[Dict[str, Any]]], None]] = None

        self._ws_clients: Set[Any] = set()
        self._serial_reader: Optional[DirectSerialReader] = None
        if self.mode == "serial":
            self._serial_reader = DirectSerialReader(
                on_frame=self.ingest_arduino_frame,
                on_disconnect=self.mark_arduino_disconnected_immediately,
                preferred_port=serial_port,
            )

    def register_ros2_publish_hook(
        self, hook: Callable[[Dict[str, Any], List[Dict[str, Any]]], None]
    ) -> None:
        self._ros2_hook = hook

    # --------------------- Thread-Safe Ingestion API ---------------------

    def record_arduino_heartbeat(
        self,
        port: str = "/dev/ttyACM0",
        seq: Optional[int] = None,
        now_mono: Optional[float] = None,
    ) -> None:
        t = now_mono if now_mono is not None else time.monotonic()
        with self._lock:
            self._last_heartbeat_mono = t
            self._arduino_forced_off = False
            self._serial_port_label = port
            if seq is not None:
                self._frame_seq = seq

    def mark_arduino_disconnected_immediately(self) -> None:
        with self._lock:
            self._arduino_forced_off = True

    def ingest_single_sensor(
        self,
        sensor_id: str,
        value: Optional[float],
        is_ok: bool = True,
        now_mono: Optional[float] = None,
    ) -> None:
        if sensor_id not in SENSOR_IDS:
            return
        t = now_mono if now_mono is not None else time.monotonic()
        with self._lock:
            self._last_heartbeat_mono = t
            self._arduino_forced_off = False
            if is_ok and value is not None:
                self._sensor_latest_val[sensor_id] = float(value)
                self._sensor_latest_mono[sensor_id] = t
                self._sensor_explicit_ok[sensor_id] = True
            else:
                self._sensor_explicit_ok[sensor_id] = False
                self._sensor_latest_val[sensor_id] = None

    def update_flame_intensity(
        self, intensity_pct: Optional[float], analog_raw: Optional[int] = None
    ) -> None:
        with self._lock:
            if intensity_pct is not None:
                self._flame_extra["intensity_pct"] = round(float(intensity_pct), 1)
            if analog_raw is not None:
                self._flame_extra["analog_raw"] = int(analog_raw)

    def ingest_arduino_frame(
        self, frame: Dict[str, Any], now_mono: Optional[float] = None
    ) -> None:
        t = now_mono if now_mono is not None else time.monotonic()
        self.record_arduino_heartbeat(
            port=frame.get("port", self._serial_port_label),
            seq=frame.get("seq"),
            now_mono=t,
        )

        ds = frame.get("ds18b20") or {}
        self.ingest_single_sensor(
            "ds18b20_temp",
            ds.get("temp_c"),
            is_ok=bool(ds.get("ok") and ds.get("temp_c") is not None),
            now_mono=t,
        )

        dht = frame.get("dht11") or {}
        dht_ok = bool(dht.get("ok"))
        self.ingest_single_sensor(
            "dht11_temp",
            dht.get("temp_c"),
            is_ok=bool(dht_ok and dht.get("temp_c") is not None),
            now_mono=t,
        )
        self.ingest_single_sensor(
            "dht11_humidity",
            dht.get("humidity_pct"),
            is_ok=bool(dht_ok and dht.get("humidity_pct") is not None),
            now_mono=t,
        )

        fl = frame.get("flame") or {}
        fl_ok = bool(fl.get("ok") and fl.get("detected") is not None)
        self.ingest_single_sensor(
            "flame_sensor",
            float(fl["detected"]) if fl_ok else None,
            is_ok=fl_ok,
            now_mono=t,
        )
        if fl_ok:
            self.update_flame_intensity(fl.get("intensity_pct"), fl.get("analog_raw"))

    # --------------------- Deterministic Evaluation Step ---------------------

    def step_once(
        self,
        now_mono: Optional[float] = None,
        ts_ms: Optional[int] = None,
    ) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Executes a single collection + health evaluation + SQLite persistence step."""
        t_mono = now_mono if now_mono is not None else time.monotonic()
        wall_ms = ts_ms if ts_ms is not None else int(time.time() * 1000)

        if self.mode == "simulator":
            sim_frame = self.simulator.next_frame()
            if sim_frame is not None:
                self.ingest_arduino_frame(sim_frame, now_mono=t_mono)

        cfg = self.thresholds
        with self._lock:
            hb_age = (
                (t_mono - self._last_heartbeat_mono)
                if self._last_heartbeat_mono is not None
                else 9999.0
            )
            arduino_connected = (not self._arduino_forced_off) and (
                hb_age <= cfg.arduino_timeout_sec
            )
            port_label = self._serial_port_label
            seq = self._frame_seq

            sensor_inputs: Dict[str, Dict[str, Any]] = {}
            for sid in SENSOR_IDS:
                s_mono = self._sensor_latest_mono[sid]
                s_age = (t_mono - s_mono) if s_mono is not None else 9999.0
                s_valid = (
                    arduino_connected
                    and self._sensor_explicit_ok[sid]
                    and self._sensor_latest_val[sid] is not None
                    and (s_age <= cfg.sensor_timeout_sec)
                )
                extra = dict(self._flame_extra) if sid == "flame_sensor" else {}
                sensor_inputs[sid] = {
                    "value": self._sensor_latest_val[sid] if s_valid else None,
                    "is_valid": s_valid,
                    "age_sec": round(s_age, 2) if s_mono is not None else None,
                    "extra": extra,
                }

        snapshot, alert_events = self.engine.evaluate_cycle(
            now_sec=t_mono,
            ts_ms=wall_ms,
            arduino_connected=arduino_connected,
            sensor_inputs=sensor_inputs,
        )

        # Persist continuously to SQLite (runs regardless of whether dashboard is open!)
        row_id = self.db.insert_reading(snapshot)
        serialized_alerts: List[Dict[str, Any]] = []
        for ev in alert_events:
            self.db.insert_alert(ev)
            serialized_alerts.append(ev.to_dict())

        stats = self.db.get_storage_stats()
        snapshot["reading_id"] = row_id
        snapshot["ingestion_mode"] = self.mode
        snapshot["arduino_port"] = port_label
        snapshot["frame_seq"] = seq
        snapshot["storage_stats"] = stats
        if self.mode == "simulator":
            snapshot["simulator"] = self.simulator.get_status()

        with self._lock:
            self._latest_snapshot = snapshot
            self._recent_ring.append(self._compact_point(snapshot))

        if self._ros2_hook is not None:
            try:
                self._ros2_hook(snapshot, serialized_alerts)
            except Exception:
                pass

        return snapshot, serialized_alerts

    @staticmethod
    def _compact_point(snap: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "timestamp_ms": snap["timestamp_ms"],
            "timestamp_iso": snap["timestamp_iso"],
            "arduino_connected": snap["arduino_connected"],
            "sensors": {
                sid: {
                    "display_value": snap["sensors"][sid]["display_value"],
                    "raw_value": snap["sensors"][sid]["raw_value"],
                    "is_valid": snap["sensors"][sid]["is_valid"],
                    "health_status": snap["sensors"][sid]["health_status"],
                }
                for sid in SENSOR_IDS
            },
        }

    # --------------------- Background Async Loop & WS Fan-out ---------------------

    async def run_continuous_loop(self, stop_event: asyncio.Event) -> None:
        if self._serial_reader is not None:
            self._serial_reader.start()
        try:
            while not stop_event.is_set():
                t0 = time.monotonic()
                snapshot, new_alerts = await asyncio.to_thread(self.step_once)
                await self._broadcast_ws(
                    {
                        "type": "tick",
                        "snapshot": snapshot,
                        "new_alerts": new_alerts,
                    }
                )
                elapsed = time.monotonic() - t0
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=max(0.1, 1.0 - elapsed))
                except asyncio.TimeoutError:
                    pass
        finally:
            if self._serial_reader is not None:
                self._serial_reader.stop()

    async def register_ws(self, ws: Any) -> None:
        self._ws_clients.add(ws)
        init_payload = await asyncio.to_thread(self.build_initial_bundle)
        await ws.send_json(init_payload)

    def unregister_ws(self, ws: Any) -> None:
        self._ws_clients.discard(ws)

    async def _broadcast_ws(self, message: Dict[str, Any]) -> None:
        if not self._ws_clients:
            return
        dead = []
        for ws in list(self._ws_clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._ws_clients.discard(ws)

    def build_initial_bundle(self) -> Dict[str, Any]:
        with self._lock:
            snap = self._latest_snapshot
            ring = list(self._recent_ring)
        recent_alerts = self.db.query_alerts(limit=50)
        return {
            "type": "init",
            "sensor_catalog": SENSOR_CATALOG,
            "snapshot": snap,
            "recent_series": ring,
            "recent_alerts": recent_alerts,
            "thresholds": self.thresholds.model_dump(),
            "simulator": self.simulator.get_status() if self.mode == "simulator" else None,
        }

    def update_thresholds(self, payload: Dict[str, Any]) -> ThresholdsConfig:
        merged = {**self.thresholds.model_dump(), **payload}
        new_cfg = ThresholdsConfig(**merged)
        save_thresholds(self.thresholds_path, new_cfg)
        self.thresholds = new_cfg
        self.engine.update_thresholds(new_cfg)
        return new_cfg
