import asyncio
from contextlib import asynccontextmanager
import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.config import SENSOR_CATALOG
from backend.pipeline import MonitoringPipeline


class ScenarioRequest(BaseModel):
    scenario: str


def create_app(existing_pipeline: Optional[MonitoringPipeline] = None) -> FastAPI:
    mode = os.environ.get("PCM_MODE", "simulator")
    db_path = os.environ.get("PCM_DB_PATH", "/home/user/backend/data/sensor_monitoring.db")
    thresholds_path = os.environ.get(
        "PCM_THRESHOLDS_PATH", "/home/user/backend/data/thresholds.json"
    )
    serial_port = os.environ.get("PCM_SERIAL_PORT", "/dev/ttyACM0")

    pipeline = existing_pipeline or MonitoringPipeline(
        mode=mode,
        db_path=db_path,
        thresholds_path=thresholds_path,
        serial_port=serial_port,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop_event = asyncio.Event()
        # Prime one immediate step so /api/state has data right at startup
        await asyncio.to_thread(pipeline.step_once)
        loop_task = asyncio.create_task(pipeline.run_continuous_loop(stop_event))
        app.state.pipeline = pipeline
        try:
            yield
        finally:
            stop_event.set()
            await loop_task

    app = FastAPI(
        title="Predictive Condition Monitoring & Maintenance Analytics API",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    async def root_info() -> Dict[str, Any]:
        stats = await asyncio.to_thread(pipeline.db.get_storage_stats)
        return {
            "service": "Predictive Condition Monitoring & Maintenance Analytics API",
            "mode": pipeline.mode,
            "websocket": "/ws/live",
            "endpoints": ["/api/health", "/api/state", "/api/history", "/api/alerts", "/api/config"],
            "storage": stats,
        }

    @app.get("/api/health")
    async def get_health() -> Dict[str, Any]:
        stats = await asyncio.to_thread(pipeline.db.get_storage_stats)
        return {
            "status": "ok",
            "mode": pipeline.mode,
            "storage": stats,
        }

    @app.get("/api/sensors/meta")
    async def get_sensor_meta() -> Dict[str, Any]:
        return {"sensors": SENSOR_CATALOG}

    @app.get("/api/state")
    async def get_state() -> Dict[str, Any]:
        return await asyncio.to_thread(pipeline.build_initial_bundle)

    @app.get("/api/history")
    async def get_history(
        start_ms: Optional[int] = Query(default=None),
        end_ms: Optional[int] = Query(default=None),
        minutes: Optional[float] = Query(default=None, ge=0.5, le=10080.0),
        limit: int = Query(default=600, ge=10, le=5000),
        max_points: int = Query(default=240, ge=20, le=1000),
        missing_only: bool = Query(default=False),
    ) -> Dict[str, Any]:
        import time as _time

        eff_start = start_ms
        if eff_start is None and minutes is not None:
            eff_start = int((_time.time() - minutes * 60.0) * 1000)
        return await asyncio.to_thread(
            pipeline.db.query_history,
            eff_start,
            end_ms,
            limit,
            max_points,
            missing_only,
        )

    @app.get("/api/alerts")
    async def get_alerts(
        start_ms: Optional[int] = Query(default=None),
        end_ms: Optional[int] = Query(default=None),
        domain: Optional[str] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> Dict[str, Any]:
        items = await asyncio.to_thread(
            pipeline.db.query_alerts, start_ms, end_ms, domain, limit
        )
        return {"alerts": items, "count": len(items)}

    @app.get("/api/config")
    async def get_config() -> Dict[str, Any]:
        return {"thresholds": pipeline.thresholds.model_dump()}

    @app.put("/api/config")
    async def put_config(payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            updated = await asyncio.to_thread(pipeline.update_thresholds, payload)
            return {"thresholds": updated.model_dump()}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/simulator/scenario")
    async def set_simulator_scenario(req: ScenarioRequest) -> Dict[str, Any]:
        if pipeline.mode != "simulator":
            raise HTTPException(
                status_code=400,
                detail=f"Scenario injection is only active in simulator mode (current mode: {pipeline.mode}).",
            )
        try:
            status = pipeline.simulator.set_scenario(req.scenario)
            if req.scenario == "arduino_disconnected":
                pipeline.mark_arduino_disconnected_immediately()
            return {"status": "ok", "simulator": status}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.websocket("/ws/live")
    async def websocket_live(ws: WebSocket) -> None:
        await ws.accept()
        await pipeline.register_ws(ws)
        try:
            while True:
                # Keep connection alive and allow optional client ping messages
                msg = await ws.receive_text()
                if msg == "ping":
                    await ws.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pipeline.unregister_ws(ws)
        except Exception:
            pipeline.unregister_ws(ws)

    return app


app = create_app()
