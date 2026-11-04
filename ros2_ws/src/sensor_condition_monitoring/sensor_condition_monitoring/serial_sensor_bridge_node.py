#!/usr/bin/env python3
"""
ROS 2 Humble Node: serial_sensor_bridge_node
Reads newline-delimited JSON frames from Arduino Uno over USB Serial (/dev/ttyACM0 or /dev/ttyUSB0)
and publishes individual sensor measurements onto dedicated ROS 2 Humble topics.

Topics Published:
  - /sensors/ds18b20/temperature  (sensor_msgs/msg/Temperature)      [only when DS18B20 is valid]
  - /sensors/dht11/temperature    (sensor_msgs/msg/Temperature)      [only when DHT11 is valid]
  - /sensors/dht11/humidity       (sensor_msgs/msg/RelativeHumidity) [only when DHT11 is valid]
  - /sensors/flame/detected       (std_msgs/msg/Bool)                [only when Flame sensor is valid]
  - /sensors/flame/intensity      (std_msgs/msg/Float32)             [only when Flame sensor is valid]
  - /arduino/heartbeat            (std_msgs/msg/String)              [connection + seq heartbeat]
  - /sensors/arduino_frame        (std_msgs/msg/String)              [raw parsed JSON frame]
"""

import json
import os
import time
from typing import List, Optional

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import RelativeHumidity, Temperature
from std_msgs.msg import Bool, Float32, String

try:
    import serial
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("pyserial is required: sudo apt install python3-serial") from exc


class SerialSensorBridgeNode(Node):
    def __init__(self) -> None:
        super().__init__("serial_bridge")

        self.declare_parameter("serial_port", "/dev/ttyACM0")
        self.declare_parameter(
            "fallback_ports", ["/dev/ttyACM0", "/dev/ttyACM1", "/dev/ttyUSB0", "/dev/ttyUSB1"]
        )
        self.declare_parameter("baud_rate", 115200)
        self.declare_parameter("serial_timeout_sec", 1.5)
        self.declare_parameter("reconnect_interval_sec", 2.0)

        self.preferred_port: str = (
            self.get_parameter("serial_port").get_parameter_value().string_value
        )
        self.fallback_ports: List[str] = list(
            self.get_parameter("fallback_ports").get_parameter_value().string_array_value
        )
        self.baud_rate: int = self.get_parameter("baud_rate").get_parameter_value().integer_value
        self.serial_timeout: float = (
            self.get_parameter("serial_timeout_sec").get_parameter_value().double_value
        )
        self.reconnect_interval: float = (
            self.get_parameter("reconnect_interval_sec").get_parameter_value().double_value
        )

        # Dedicated ROS 2 publishers per physical measurement
        self.pub_ds18b20_temp = self.create_publisher(
            Temperature, "/sensors/ds18b20/temperature", 10
        )
        self.pub_dht11_temp = self.create_publisher(
            Temperature, "/sensors/dht11/temperature", 10
        )
        self.pub_dht11_humidity = self.create_publisher(
            RelativeHumidity, "/sensors/dht11/humidity", 10
        )
        self.pub_flame_detected = self.create_publisher(Bool, "/sensors/flame/detected", 10)
        self.pub_flame_intensity = self.create_publisher(Float32, "/sensors/flame/intensity", 10)
        self.pub_heartbeat = self.create_publisher(String, "/arduino/heartbeat", 10)
        self.pub_raw_frame = self.create_publisher(String, "/sensors/arduino_frame", 10)

        self._ser: Optional[serial.Serial] = None
        self._active_port: Optional[str] = None
        self._last_reconnect_attempt: float = 0.0
        self._last_disconnect_notice: float = 0.0

        # Poll serial line non-blockingly at 20 Hz
        self.timer = self.create_timer(0.05, self._poll_serial)
        self.get_logger().info(
            f"Arduino Serial Bridge started (preferred port={self.preferred_port}, baud={self.baud_rate})"
        )

    def _candidate_ports(self) -> List[str]:
        ports = [self.preferred_port] + [
            p for p in self.fallback_ports if p != self.preferred_port
        ]
        return [p for p in ports if p]

    def _try_connect(self) -> bool:
        now = time.monotonic()
        if now - self._last_reconnect_attempt < self.reconnect_interval:
            return False
        self._last_reconnect_attempt = now

        for port in self._candidate_ports():
            if not os.path.exists(port):
                continue
            try:
                self._ser = serial.Serial(
                    port=port,
                    baudrate=self.baud_rate,
                    timeout=0.05,
                )
                self._active_port = port
                self.get_logger().info(f"Connected to Arduino Uno on {port}")
                return True
            except (serial.SerialException, OSError) as err:
                self.get_logger().debug(f"Could not open {port}: {err}")

        if now - self._last_disconnect_notice >= 5.0:
            self._last_disconnect_notice = now
            self._publish_heartbeat(connected=False, seq=None)
            self.get_logger().warn(
                f"Arduino Uno serial device not detected on {self._candidate_ports()}"
            )
        return False

    def _close_serial(self, reason: str) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
        port = self._active_port or self.preferred_port
        self._ser = None
        self._active_port = None
        self._publish_heartbeat(connected=False, seq=None)
        self.get_logger().error(f"Arduino Uno serial connection lost ({port}): {reason}")

    def _publish_heartbeat(self, connected: bool, seq: Optional[int]) -> None:
        msg = String()
        msg.data = json.dumps(
            {
                "connected": connected,
                "port": self._active_port or self.preferred_port,
                "seq": seq,
                "timestamp_ms": int(time.time() * 1000),
            }
        )
        self.pub_heartbeat.publish(msg)

    def _poll_serial(self) -> None:
        if self._ser is None or not self._ser.is_open:
            self._try_connect()
            return

        try:
            while self._ser is not None and self._ser.in_waiting > 0:
                raw_bytes = self._ser.readline()
                if not raw_bytes:
                    break
                line = raw_bytes.decode("utf-8", errors="ignore").strip()
                if not line or not line.startswith("{"):
                    continue
                self._handle_json_line(line)
        except (serial.SerialException, OSError) as err:
            self._close_serial(str(err))

    def _handle_json_line(self, line: str) -> None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            self.get_logger().warn(f"Ignored malformed serial frame: {line[:120]}")
            return

        ros_now = self.get_clock().now().to_msg()
        seq = payload.get("seq")
        self._publish_heartbeat(connected=True, seq=seq)

        frame_msg = String()
        frame_msg.data = json.dumps(payload)
        self.pub_raw_frame.publish(frame_msg)

        # 1. DS18B20 Temperature (published ONLY when sensor reading is valid)
        ds_block = payload.get("ds18b20") or {}
        if ds_block.get("ok") is True and ds_block.get("temp_c") is not None:
            t_msg = Temperature()
            t_msg.header.stamp = ros_now
            t_msg.header.frame_id = "ds18b20_link"
            t_msg.temperature = float(ds_block["temp_c"])
            t_msg.variance = 0.04  # +/-0.2C typical variance
            self.pub_ds18b20_temp.publish(t_msg)

        # 2. DHT11 Temperature & Humidity (separate topics, published ONLY when valid)
        dht_block = payload.get("dht11") or {}
        if dht_block.get("ok") is True:
            if dht_block.get("temp_c") is not None:
                dt_msg = Temperature()
                dt_msg.header.stamp = ros_now
                dt_msg.header.frame_id = "dht11_link"
                dt_msg.temperature = float(dht_block["temp_c"])
                dt_msg.variance = 1.0  # +/-1.0C typical DHT11 variance
                self.pub_dht11_temp.publish(dt_msg)

            if dht_block.get("humidity_pct") is not None:
                rh_msg = RelativeHumidity()
                rh_msg.header.stamp = ros_now
                rh_msg.header.frame_id = "dht11_link"
                # REP-103: RelativeHumidity uses 0.0..1.0 ratio
                rh_msg.relative_humidity = float(dht_block["humidity_pct"]) / 100.0
                rh_msg.variance = 0.0025
                self.pub_dht11_humidity.publish(rh_msg)

        # 3. Flame Sensor (published ONLY when valid)
        flame_block = payload.get("flame") or {}
        if flame_block.get("ok") is True and flame_block.get("detected") is not None:
            fl_msg = Bool()
            fl_msg.data = bool(int(flame_block["detected"]) == 1)
            self.pub_flame_detected.publish(fl_msg)

            if flame_block.get("intensity_pct") is not None:
                inten_msg = Float32()
                inten_msg.data = float(flame_block["intensity_pct"])
                self.pub_flame_intensity.publish(inten_msg)

    def destroy_node(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
        super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = SerialSensorBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
