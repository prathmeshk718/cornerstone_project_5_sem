import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    pkg_share = get_package_share_directory("sensor_condition_monitoring")
    default_params = os.path.join(pkg_share, "config", "ros2_params.yaml")

    serial_port_arg = DeclareLaunchArgument(
        "serial_port",
        default_value="/dev/ttyACM0",
        description="Arduino Uno USB Serial device path on Ubuntu 22.04",
    )
    use_mock_arg = DeclareLaunchArgument(
        "use_mock_arduino",
        default_value="false",
        description="Publish simulated ROS 2 sensor topics instead of reading physical USB serial",
    )
    params_file_arg = DeclareLaunchArgument(
        "params_file",
        default_value=default_params,
        description="Path to ROS 2 YAML parameters file",
    )

    serial_bridge_node = Node(
        package="sensor_condition_monitoring",
        executable="serial_bridge",
        name="serial_bridge",
        output="screen",
        parameters=[
            LaunchConfiguration("params_file"),
            {"serial_port": LaunchConfiguration("serial_port")},
        ],
        condition=UnlessCondition(LaunchConfiguration("use_mock_arduino")),
    )

    mock_publisher_node = Node(
        package="sensor_condition_monitoring",
        executable="mock_publisher",
        name="mock_publisher",
        output="screen",
        condition=IfCondition(LaunchConfiguration("use_mock_arduino")),
    )

    analytics_server_node = Node(
        package="sensor_condition_monitoring",
        executable="analytics_server",
        name="analytics_server",
        output="screen",
        parameters=[LaunchConfiguration("params_file")],
    )

    return LaunchDescription(
        [
            serial_port_arg,
            use_mock_arg,
            params_file_arg,
            serial_bridge_node,
            mock_publisher_node,
            analytics_server_node,
        ]
    )
