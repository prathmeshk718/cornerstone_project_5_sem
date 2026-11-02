import asyncio
from contextlib import asynccontextmanager
import json
import os
import re
import sys
import time
from typing import Dict, Any, List, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from data_pipeline.sensor_logger import SensorDataLogger
from data_pipeline.serial_and_sim_source import (
    SensorDataSource,
    seed_initial_historical_dataset,
)
from ml_framework.pipeline import PredictiveConditionMonitoringFramework

CONFIG_PATH = os.path.join(WORKSPACE_ROOT, "config/hardware_config.json")
ARDUINO_INO_PATH = os.path.join(
    WORKSPACE_ROOT, "arduino/sensor_acquisition/sensor_acquisition.ino"
)
CSV_HISTORY_PATH = os.path.join(WORKSPACE_ROOT, "data/sensor_readings_history.csv")
SQLITE_DB_PATH = os.path.join(WORKSPACE_ROOT, "data/condition_monitoring.db")
SESSION_DIR = os.path.join(WORKSPACE_ROOT, "data/logged_sessions")
MODEL_DIR = os.path.join(WORKSPACE_ROOT, "data/models")


def load_hardware_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_hardware_config(cfg: Dict[str, Any]) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def sync_arduino_sketch_pins(cfg: Dict[str, Any]) -> None:
    """
    Updates the #define constants in arduino/sensor_acquisition/sensor_acquisition.ino
    whenever the user updates their custom pin mappings or smoke sensor model.
    """
    if not os.path.exists(ARDUINO_INO_PATH):
        return

    hw = cfg.get("hardware", {})
    pins = hw.get("pins", {})
    smoke_model = (
        hw.get("sensors", {})
        .get("smoke", {})
        .get("model", "ANALOG_SMOKE")
    )
    baud_rate = hw.get("baud_rate", 115200)
    interval_ms = hw.get("sampling_interval_ms", 1000)

    def _clean_digital_pin(val: str) -> str:
        val = str(val).strip().upper()
        if val.startswith("D") and val[1:].isdigit():
            return val[1:]
        return val

    ds_pin = _clean_digital_pin(pins.get("ds18b20_pin", "D2"))
    dht_pin = _clean_digital_pin(pins.get("dht11_pin", "D3"))
    yg_d_pin = _clean_digital_pin(pins.get("yg1006_digital_pin", "D4"))
    yg_a_pin = str(pins.get("yg1006_analog_pin", "A1")).strip().upper()
    smoke_pin = str(pins.get("smoke_analog_pin", "A0")).strip().upper()

    with open(ARDUINO_INO_PATH, "r", encoding="utf-8") as f:
        code = f.read()

    replacements = [
        (r"(#define\s+PIN_DS18B20\s+)\S+", f"\\g<1>{ds_pin}"),
        (r"(#define\s+PIN_DHT11\s+)\S+", f"\\g<1>{dht_pin}"),
        (r"(#define\s+PIN_YG1006_DIGITAL\s+)\S+", f"\\g<1>{yg_d_pin}"),
        (r"(#define\s+PIN_YG1006_ANALOG\s+)\S+", f"\\g<1>{yg_a_pin}"),
        (r"(#define\s+PIN_SMOKE_ANALOG\s+)\S+", f"\\g<1>{smoke_pin}"),
        (r'(#define\s+SMOKE_SENSOR_MODEL\s+)"[^"]*"', f'\\g<1>"{smoke_model}"'),
        (r"(#define\s+SERIAL_BAUD_RATE\s+)\d+", f"\\g<1>{int(baud_rate)}"),
        (r"(#define\s+SAMPLE_INTERVAL_MS\s+)\d+", f"\\g<1>{int(interval_ms)}"),
    ]

    for pattern, repl in replacements:
        code = re.sub(pattern, repl, code)

    with open(ARDUINO_INO_PATH, "w", encoding="utf-8") as f:
        f.write(code)


# Global Singletons
hardware_config = load_hardware_config()
data_logger = SensorDataLogger(
    csv_path=CSV_HISTORY_PATH,
    sqlite_path=SQLITE_DB_PATH,
    session_dir=SESSION_DIR,
)
seed_initial_historical_dataset(data_logger, num_samples=300)

ml_framework = PredictiveConditionMonitoringFramework(model_dir=MODEL_DIR)
ml_framework.train_and_evaluate(CSV_HISTORY_PATH)

data_source = SensorDataSource(
    serial_port=hardware_config["hardware"].get("serial_port", "/dev/ttyACM0"),
    baud_rate=hardware_config["hardware"].get("baud_rate", 115200),
    prefer_serial=False,
)

active_websockets: Set[WebSocket] = set()
latest_packet: Dict[str, Any] = {}
recent_stream_buffer: List[Dict[str, Any]] = []
MAX_STREAM_BUFFER = 60


async def telemetry_loop():
    """
    Continuous 1 Hz acquisition, ROS 2 topic mirroring, preprocessing,
    ML inference, CSV/SQLite logging, and WebSocket broadcast loop.
    """
    global latest_packet
    while True:
        try:
            raw_frame = data_source.read_next_frame()
            analytics = ml_framework.analyze_realtime_frame(raw_frame)

            # Auto-align operator label if in simulator mode unless manually overridden
            logged_record = data_logger.log_reading(
                raw_frame, analytics_result=analytics
            )

            packet = {
                "type": "telemetry_update",
                "timestamp_unix": raw_frame["timestamp_unix"],
                "iso_timestamp": logged_record["iso_timestamp"]
                if logged_record
                else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "seq": raw_frame["seq"],
                "connection": {
                    "websocket_connected": True,
                    "source_mode": data_source.mode,
                    "scenario": data_source.scenario,
                    "serial_port": data_source.serial_port,
                    "baud_rate": data_source.baud_rate,
                    "serial_connected": data_source.serial_connected,
                    "ros2_bridge_status": "ACTIVE (Humble Topic Schema)",
                },
                "sensors": {
                    "ds18b20_temp_c": raw_frame["ds18b20_temp_c"],
                    "dht11_temp_c": raw_frame["dht11_temp_c"],
                    "dht11_humidity_pct": raw_frame["dht11_humidity_pct"],
                    "yg1006_flame": raw_frame["yg1006_flame"],
                    "yg1006_ir_raw": raw_frame["yg1006_ir_raw"],
                    "smoke_raw_adc": raw_frame["smoke_raw_adc"],
                    "temp_differential_c": analytics["preprocessed_features"][
                        "temp_differential_c"
                    ],
                },
                "analytics": analytics,
                "logger": data_logger.get_status(),
            }

            latest_packet = packet
            recent_stream_buffer.append(
                {
                    "seq": packet["seq"],
                    "time_label": time.strftime(
                        "%H:%M:%S", time.localtime(raw_frame["timestamp_unix"])
                    ),
                    "ds18b20_temp_c": raw_frame["ds18b20_temp_c"],
                    "dht11_temp_c": raw_frame["dht11_temp_c"],
                    "dht11_humidity_pct": raw_frame["dht11_humidity_pct"],
                    "yg1006_flame": raw_frame["yg1006_flame"],
                    "smoke_raw_adc": raw_frame["smoke_raw_adc"],
                    "temp_differential_c": analytics["preprocessed_features"][
                        "temp_differential_c"
                    ],
                    "risk_level": analytics["risk_level"],
                }
            )
            if len(recent_stream_buffer) > MAX_STREAM_BUFFER:
                recent_stream_buffer.pop(0)

            # Broadcast to all connected React dashboard clients
            if active_websockets:
                msg_text = json.dumps(packet)
                stale: List[WebSocket] = []
                for ws in list(active_websockets):
                    try:
                        await ws.send_text(msg_text)
                    except Exception:
                        stale.append(ws)
                for ws in stale:
                    active_websockets.discard(ws)

        except Exception as exc:
            print(f"[TelemetryLoop Error]: {exc}", flush=True)

        interval_sec = (
            hardware_config.get("hardware", {}).get("sampling_interval_ms", 1000)
            / 1000.0
        )
        await asyncio.sleep(max(0.25, interval_sec))


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(telemetry_loop())
    yield
    task.cancel()


app = FastAPI(
    title="Predictive Condition Monitoring & Maintenance Analytics API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/status")
async def get_system_status():
    return {
        "hardware_config": hardware_config,
        "source": data_source.get_status(),
        "logger": data_logger.get_status(),
        "historical_summary": data_logger.get_historical_summary(),
        "ml_evaluation": ml_framework.evaluation_report,
        "latest_packet": latest_packet,
        "recent_stream": recent_stream_buffer,
    }


@app.post("/api/source/configure")
async def configure_source(payload: Dict[str, Any] = Body(...)):
    mode = payload.get("mode")
    scenario = payload.get("scenario")
    serial_port = payload.get("serial_port")
    baud_rate = payload.get("baud_rate")

    status = data_source.configure(
        mode=mode,
        scenario=scenario,
        serial_port=serial_port,
        baud_rate=baud_rate,
    )

    # Optionally update operator label to match scenario when testing simulator
    if scenario and payload.get("sync_operator_label", True):
        scen_info = status.get("scenario_info", {})
        if "default_label" in scen_info:
            data_logger.set_operator_label(int(scen_info["default_label"]))

    return {"status": "ok", "source": status, "logger": data_logger.get_status()}


@app.post("/api/logger/control")
async def control_logger(payload: Dict[str, Any] = Body(...)):
    action = payload.get("action")
    if action == "set_recording":
        data_logger.set_recording(bool(payload.get("is_recording", True)))
    elif action == "set_label":
        data_logger.set_operator_label(int(payload.get("operator_label", 0)))
    elif action == "new_session":
        reset_all = bool(payload.get("reset_main_csv", False))
        data_logger.start_new_session(reset_main_csv=reset_all)
    return {
        "status": "ok",
        "logger": data_logger.get_status(),
        "historical_summary": data_logger.get_historical_summary(),
    }


@app.get("/api/logger/download")
async def download_logged_csv(scope: str = "all"):
    target_path = (
        data_logger.current_session_csv
        if scope == "session"
        else data_logger.csv_path
    )
    filename = os.path.basename(target_path)
    return FileResponse(
        path=target_path,
        media_type="text/csv",
        filename=filename,
    )


@app.post("/api/ml/retrain")
async def retrain_ml_models():
    try:
        report = ml_framework.train_and_evaluate(CSV_HISTORY_PATH)
        return {
            "status": "ok",
            "ml_evaluation": report,
            "historical_summary": data_logger.get_historical_summary(),
        }
    except Exception as exc:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": str(exc)},
        )


@app.post("/api/config/hardware")
async def update_hardware_config(payload: Dict[str, Any] = Body(...)):
    global hardware_config
    hw = hardware_config.setdefault("hardware", {})
    pins = hw.setdefault("pins", {})

    if "pins" in payload and isinstance(payload["pins"], dict):
        for k in (
            "ds18b20_pin",
            "dht11_pin",
            "yg1006_digital_pin",
            "yg1006_analog_pin",
            "smoke_analog_pin",
        ):
            if k in payload["pins"] and str(payload["pins"][k]).strip():
                pins[k] = str(payload["pins"][k]).strip()

    if "serial_port" in payload and str(payload["serial_port"]).strip():
        hw["serial_port"] = str(payload["serial_port"]).strip()
        data_source.serial_port = hw["serial_port"]

    if "baud_rate" in payload:
        hw["baud_rate"] = int(payload["baud_rate"])
        data_source.baud_rate = hw["baud_rate"]

    if "sampling_interval_ms" in payload:
        hw["sampling_interval_ms"] = max(250, int(payload["sampling_interval_ms"]))

    if "smoke_sensor_model" in payload and str(payload["smoke_sensor_model"]).strip():
        hw.setdefault("sensors", {}).setdefault("smoke", {})["model"] = str(
            payload["smoke_sensor_model"]
        ).strip()

    save_hardware_config(hardware_config)
    sync_arduino_sketch_pins(hardware_config)

    return {
        "status": "ok",
        "hardware_config": hardware_config,
        "message": "Hardware pinout, smoke sensor model, and Arduino .ino firmware updated.",
    }


@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    await websocket.accept()
    active_websockets.add(websocket)
    try:
        if latest_packet:
            await websocket.send_text(json.dumps(latest_packet))
        while True:
            # Keep connection alive and handle optional client commands
            data = await websocket.receive_text()
            try:
                cmd = json.loads(data)
                if cmd.get("action") == "set_scenario":
                    data_source.configure(scenario=cmd.get("scenario"))
            except Exception:
                pass
    except WebSocketDisconnect:
        active_websockets.discard(websocket)
    except Exception:
        active_websockets.discard(websocket)


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("BACKEND_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
