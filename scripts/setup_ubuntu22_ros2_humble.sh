#!/usr/bin/env bash
set -euo pipefail

# Ubuntu 22.04 + ROS 2 Humble Workspace Setup Script
# Installs Python & Node dependencies, grants USB serial permissions, and builds the ROS 2 package.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[1/4] Checking ROS 2 Humble environment..."
if [ -f /opt/ros/humble/setup.bash ]; then
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
else
  echo "WARNING: /opt/ros/humble/setup.bash not found. Install ros-humble-desktop on Ubuntu 22.04 first."
fi

echo "[2/4] Installing Python backend dependencies..."
python3 -m pip install -r "${PROJECT_ROOT}/backend/requirements.txt"

echo "[3/4] Installing React + Vite dashboard dependencies..."
cd "${PROJECT_ROOT}/dashboard"
npm install

if command -v colcon >/dev/null 2>&1; then
  echo "[4/4] Building ROS 2 Humble workspace with colcon..."
  cd "${PROJECT_ROOT}/ros2_ws"
  colcon build --symlink-install
  echo "ROS 2 workspace built successfully."
else
  echo "[4/4] colcon not found in PATH; skipping ROS 2 build (simulator/serial modes ready)."
fi

echo ""
echo "NOTE: For physical Arduino Uno USB access on Ubuntu 22.04 (/dev/ttyACM0), ensure your user is in 'dialout':"
echo "  sudo usermod -aG dialout \$USER && newgrp dialout"
