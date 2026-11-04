from glob import glob
import os
from setuptools import find_packages, setup

package_name = "sensor_condition_monitoring"

setup(
    name=package_name,
    version="1.0.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
    ],
    install_requires=["setuptools", "pyserial"],
    zip_safe=True,
    maintainer="Condition Monitoring Lab",
    maintainer_email="maintainer@example.com",
    description="ROS 2 Humble Arduino Uno sensor monitoring and predictive maintenance bridge",
    license="MIT",
    entry_points={
        "console_scripts": [
            "serial_bridge = sensor_condition_monitoring.serial_sensor_bridge_node:main",
            "analytics_server = sensor_condition_monitoring.ros2_analytics_node:main",
            "mock_publisher = sensor_condition_monitoring.mock_serial_publisher_node:main",
        ],
    },
)
