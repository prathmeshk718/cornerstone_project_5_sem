#!/usr/bin/env bash
set -euo pipefail

# Launch helper for Ubuntu 22.04 (ROS 2 Humble mode) or Simulator / Direct Serial mode.
# Usage:
#   ./scripts/run_system.sh ros2 /dev/ttyACM0   # Full ROS 2 Humble + Arduino Uno
#   ./scripts/run_system.sh ros2_mock           # ROS 2 Humble with mock ROS 2 publisher
#   ./scripts/run_system.sh serial /dev/ttyACM0 # Direct USB serial without ROS 2
#   ./scripts/run_system.sh simulator           # Built-in hardware & fault simulator

MODE="${1:-simulator}"
SERIAL_PORT="${2:-/dev/ttyACM0}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cleanup() {
  echo "Stopping background processes..."
  kill $(jobs -p) 2>/dev/null || true
}
trap cleanup EXIT INT TERM
if [ "${MODE}" = "ros2" ] || [ "${MODE}" = "ros2_mock" ]; then

  # Temporarily disable Bash nounset for ROS 2 setup scripts
  set +u

  source /opt/ros/humble/setup.bash
  source "${PROJECT_ROOT}/ros2_ws/install/setup.bash"

  # Restore strict Bash checking
  set -u
  if [ "${MODE}" = "ros2_mock" ]; then
    ros2 launch sensor_condition_monitoring monitoring_system.launch.py use_mock_arduino:=true &
  else
    ros2 launch sensor_condition_monitoring monitoring_system.launch.py serial_port:="${SERIAL_PORT}" &
  fi
else
  export PCM_MODE="${MODE}"
  export PCM_SERIAL_PORT="${SERIAL_PORT}"
  export PYTHONPATH="${PROJECT_ROOT}"
  python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
fi

cd "${PROJECT_ROOT}/dashboard"
npm run dev
