#!/usr/bin/env python3
"""
ROS 2 Humble Node: mock_serial_publisher_node
Publishes realistic synthetic DS18B20, DHT11 (Temp & Humidity), and Flame Sensor readings
onto the exact ROS 2 Humble topics used by serial_bridge, enabling full ROS 2 graph testing
on Ubuntu 22.04 even before plugging in the physical Arduino Uno.
"""

import json
import math
import random
import time

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import RelativeHumidity, Temperature
from std_msgs.msg import Bool, Float32, String


class MockSerialPublisherNode(Node):
    def __init__(self) -> None:
        super().__init__("mock_publisher")

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

        self._seq = 0
        self._start = time.monotonic()
        self.timer = self.create_timer(1.0, self._tick)
        self.get_logger().info("Mock Arduino ROS 2 publisher started at 1 Hz")

    def _tick(self) -> None:
        self._seq += 1
        elapsed = time.monotonic() - self._start
        ros_now = self.get_clock().now().to_msg()

        hb = String()
        hb.data = json.dumps(
            {
                "connected": True,
                "port": "mock://ros2_humble",
                "seq": self._seq,
                "timestamp_ms": int(time.time() * 1000),
            }
        )
        self.pub_heartbeat.publish(hb)

        base_temp = 28.5 + 1.2 * math.sin(elapsed / 25.0)
        ds_val = round(base_temp + random.uniform(-0.12, 0.12), 2)
        dht_t_val = round(base_temp + 0.4 + random.choice([-0.5, 0.0, 0.5]), 1)
        dht_h_val = round(54.0 - 1.5 * math.sin(elapsed / 25.0) + random.choice([-1.0, 0.0, 1.0]), 1)

        msg_ds = Temperature()
        msg_ds.header.stamp = ros_now
        msg_ds.header.frame_id = "ds18b20_link"
        msg_ds.temperature = ds_val
        self.pub_ds18b20_temp.publish(msg_ds)

        msg_dt = Temperature()
        msg_dt.header.stamp = ros_now
        msg_dt.header.frame_id = "dht11_link"
        msg_dt.temperature = dht_t_val
        self.pub_dht11_temp.publish(msg_dt)

        msg_rh = RelativeHumidity()
        msg_rh.header.stamp = ros_now
        msg_rh.header.frame_id = "dht11_link"
        msg_rh.relative_humidity = dht_h_val / 100.0
        self.pub_dht11_humidity.publish(msg_rh)

        msg_fl = Bool()
        msg_fl.data = False
        self.pub_flame_detected.publish(msg_fl)

        msg_fi = Float32()
        msg_fi.data = round(6.5 + random.uniform(-1.2, 1.2), 1)
        self.pub_flame_intensity.publish(msg_fi)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MockSerialPublisherNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
