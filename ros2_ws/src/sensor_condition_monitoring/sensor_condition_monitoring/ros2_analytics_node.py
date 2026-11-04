#!/usr/bin/env python3
"""
ROS 2 Humble Node: ros2_analytics_node
Subscribes to individual ROS 2 Humble sensor topics published by serial_bridge,
feeds readings into the Python Condition Monitoring & ML Analytics Pipeline,
continuously logs into SQLite, serves WebSocket/REST to the React Dashboard,
and publishes evaluated health statuses and alerts back onto ROS 2 topics.

Subscribed Topics:
  - /sensors/ds18b20/temperature  (sensor_msgs/msg/Temperature)
  - /sensors/dht11/temperature    (sensor_msgs/msg/Temperature)
  - /sensors/dht11/humidity       (sensor_msgs/msg/RelativeHumidity)
  - /sensors/flame/detected       (std_msgs/msg/Bool)
  - /sensors/flame/intensity      (std_msgs/msg/Float32)
  - /arduino/heartbeat            (std_msgs/msg/String)

Published Topics:
  - /analytics/sensor_health      (std_msgs/msg/String)
  - /analytics/alerts             (std_msgs/msg/String)
"""

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import RelativeHumidity, Temperature
from std_msgs.msg import Bool, Float32, String
import uvicorn

# Allow importing shared backend modules when installed via colcon in ros2_ws
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.main import create_app  # noqa: E402
from backend.pipeline import MonitoringPipeline  # noqa: E402


class Ros2AnalyticsServerNode(Node):
    def __init__(self) -> None:
        super().__init__("analytics_server")

        default_db = str(PROJECT_ROOT / "backend" / "data" / "sensor_monitoring.db")
        default_thresholds = str(PROJECT_ROOT / "backend" / "data" / "thresholds.json")

        self.declare_parameter("backend_host", "0.0.0.0")
        self.declare_parameter("backend_port", 8000)
        self.declare_parameter("database_path", default_db)
        self.declare_parameter("thresholds_path", default_thresholds)
        self.declare_parameter("arduino_timeout_sec", 3.5)
        self.declare_parameter("sensor_timeout_sec", 3.5)

        host = self.get_parameter("backend_host").get_parameter_value().string_value
        port = self.get_parameter("backend_port").get_parameter_value().integer_value
        db_path = self.get_parameter("database_path").get_parameter_value().string_value
        thresholds_path = (
            self.get_parameter("thresholds_path").get_parameter_value().string_value
        )

        os.environ["PCM_MODE"] = "ros2"
        os.environ["PCM_DB_PATH"] = db_path
        os.environ["PCM_THRESHOLDS_PATH"] = thresholds_path

        self.pipeline = MonitoringPipeline(
            mode="ros2",
            db_path=db_path,
            thresholds_path=thresholds_path,
        )

        # Publishers for ROS 2 downstream consumers
        self.pub_health = self.create_publisher(String, "/analytics/sensor_health", 10)
        self.pub_alerts = self.create_publisher(String, "/analytics/alerts", 10)

        # Hook alert/state callback to re-publish onto ROS 2 graph
        self.pipeline.register_ros2_publish_hook(self._publish_analytics_to_ros2)

        # Subscribe to individual ROS 2 Humble sensor topics
        self.create_subscription(
            Temperature, "/sensors/ds18b20/temperature", self._on_ds18b20_temp, 10
        )
        self.create_subscription(
            Temperature, "/sensors/dht11/temperature", self._on_dht11_temp, 10
        )
        self.create_subscription(
            RelativeHumidity, "/sensors/dht11/humidity", self._on_dht11_humidity, 10
        )
        self.create_subscription(
            Bool, "/sensors/flame/detected", self._on_flame_detected, 10
        )
        self.create_subscription(
            Float32, "/sensors/flame/intensity", self._on_flame_intensity, 10
        )
        self.create_subscription(
            String, "/arduino/heartbeat", self._on_heartbeat, 10
        )

        # Start FastAPI + WebSocket + 1Hz Pipeline Supervisor in background thread
        self._server_thread = threading.Thread(
            target=self._run_uvicorn, args=(host, port), daemon=True
        )
        self._server_thread.start()
        self.get_logger().info(
            f"ROS 2 Analytics + WebSocket Server running on http://{host}:{port} (DB: {db_path})"
        )

    def _run_uvicorn(self, host: str, port: int) -> None:
        app = create_app(existing_pipeline=self.pipeline)
        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        asyncio.run(server.serve())

    def _on_heartbeat(self, msg: String) -> None:
        try:
            data = json.loads(msg.data)
            if data.get("connected") is True:
                self.pipeline.record_arduino_heartbeat(
                    port=data.get("port", "/dev/ttyACM0"),
                    seq=data.get("seq"),
                )
            else:
                self.pipeline.mark_arduino_disconnected_immediately()
        except json.JSONDecodeError:
            pass

    def _on_ds18b20_temp(self, msg: Temperature) -> None:
        self.pipeline.ingest_single_sensor("ds18b20_temp", float(msg.temperature))

    def _on_dht11_temp(self, msg: Temperature) -> None:
        self.pipeline.ingest_single_sensor("dht11_temp", float(msg.temperature))

    def _on_dht11_humidity(self, msg: RelativeHumidity) -> None:
        # Convert REP-103 0..1 ratio back to 0..100 %RH
        pct = float(msg.relative_humidity) * 100.0
        self.pipeline.ingest_single_sensor("dht11_humidity", round(pct, 1))

    def _on_flame_detected(self, msg: Bool) -> None:
        self.pipeline.ingest_single_sensor("flame_sensor", 1.0 if msg.data else 0.0)

    def _on_flame_intensity(self, msg: Float32) -> None:
        self.pipeline.update_flame_intensity(float(msg.data))

    def _publish_analytics_to_ros2(self, snapshot: dict, new_alerts: list) -> None:
        health_msg = String()
        health_msg.data = json.dumps(
            {
                "timestamp_iso": snapshot.get("timestamp_iso"),
                "arduino_connected": snapshot.get("arduino_connected"),
                "environmental": snapshot.get("environmental"),
                "sensors": {
                    sid: {
                        "display_value": s["display_value"],
                        "connection_status": s["connection_status"],
                        "health_status": s["health_status"],
                        "ml_anomaly_score": s["ml_anomaly_score"],
                    }
                    for sid, s in snapshot.get("sensors", {}).items()
                },
            }
        )
        self.pub_health.publish(health_msg)

        for alert in new_alerts:
            alert_msg = String()
            alert_msg.data = json.dumps(alert)
            self.pub_alerts.publish(alert_msg)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = Ros2AnalyticsServerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
