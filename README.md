# Development of a Machine Learning Framework for Predictive Condition Monitoring and Maintenance Analytics

An end-to-end sensor-based condition monitoring, structured data logging, and Machine Learning predictive maintenance system integrating **Arduino Uno**, **ROS 2 Humble (Ubuntu 22.04)**, a **Python ML & Data Processing Pipeline**, **WebSocket real-time streaming**, and a **React + Vite Monitoring Dashboard**.

---

## 1. System Architecture

```text
Physical Sensors (DS18B20, DHT11, YG1006, Smoke Sensor)
        │
        ▼
  Arduino Uno (ATmega328P)
        │  USB Serial Communication (JSON Frames @ 115200 baud)
        ▼
  ROS 2 Humble (Ubuntu 22.04)
   ├─ /sensors/ds18b20/temperature      (Equipment Temperature °C)
   ├─ /sensors/dht11/temperature        (Environmental Temperature °C)
   ├─ /sensors/dht11/humidity           (Environmental Relative Humidity %)
   ├─ /sensors/yg1006/flame_detected    (Infrared / Flame Status)
   ├─ /sensors/smoke/raw_adc            (Smoke Sensor Raw Analog 0-1023 ADC)
   └─ /sensors/telemetry_frame          (Synchronized Multi-Sensor Frame)
        │
        ▼
  Python Data Processing, CSV/SQLite Logger & Machine Learning Framework
   ├─ Data Cleaning, Missing-Value Imputation & EMA Smoothing
   ├─ Independent Sensor Feature Extraction (incl. Thermal ΔT = T_equip - T_env)
   ├─ Unsupervised Anomaly Detection (Isolation Forest)
   ├─ Supervised Multi-Class Fault Detection (Random Forest Classifier)
   ├─ Predictive Maintenance Trend Forecaster (Slope & Time-to-Threshold)
   └─ Maintenance Analytics (Condition, Risk Level, Warnings, Suggested Actions)
        │
        ▼  WebSocket (/ws/telemetry) & REST API (/api/*)
  React.js + Vite Real-Time Monitoring Dashboard
```

---

## 2. Project Directory Structure

```text
├── config/
│   └── hardware_config.json                  # Centralized Arduino pinout, sensor thresholds & ROS 2 topics
├── arduino/
│   └── sensor_acquisition/
│       └── sensor_acquisition.ino            # Arduino Uno firmware for DS18B20, DHT11, YG1006, Smoke Sensor
├── ros2_ws/
│   └── src/
│       └── predictive_condition_monitoring/  # Native ROS 2 Humble Python package (ament_python)
│           ├── package.xml
│           ├── setup.py
│           ├── launch/
│           │   └── condition_monitoring.launch.py
│           └── predictive_condition_monitoring/
│               ├── serial_sensor_node.py     # Reads Arduino USB Serial & publishes ROS 2 topics
│               ├── data_logger_node.py       # Logs ROS 2 sensor stream to CSV & SQLite
│               └── ml_analytics_node.py      # Runs ML inference & publishes /analytics/condition_report
├── data_pipeline/
│   ├── sensor_logger.py                      # Structured CSV & SQLite data logger with session/label controls
│   └── serial_and_sim_source.py              # Hardware USB Serial reader + Multi-regime hardware simulator
├── ml_framework/
│   ├── preprocessing.py                      # Cleaning, imputation, smoothing, feature extraction, scaling
│   ├── pipeline.py                           # Condition monitoring, Isolation Forest, Random Forest, evaluation
│   └── train_and_evaluate.py                 # Standalone CLI script to train & evaluate ML models on CSV logs
├── backend/
│   └── server.py                             # FastAPI + WebSocket server bridging pipeline to React dashboard
├── dashboard/                                # Simple, clean React.js + Vite monitoring dashboard
│   ├── vite.config.js
│   └── src/
│       ├── App.jsx
│       └── App.css
└── data/
    ├── sensor_readings_history.csv           # Logged multi-sensor dataset for ML training & historical analysis
    ├── condition_monitoring.db               # SQLite historical database
    ├── logged_sessions/                      # Individual recorded hardware session CSVs
    └── models/                               # Persisted scikit-learn models & evaluation_metrics.json
```

---

## 3. Hardware Components & Customizable Arduino Uno Pinout

All four sensors are treated as independent physical measurements:

| Sensor | Measurement Purpose | Signal Type | Default Arduino Uno Pin | Configurable? |
|---|---|---|---|---|
| **DS18B20** | **Equipment Temperature** (`ds18b20_temp_c`) | Digital (OneWire) | `D2` | Yes (UI, JSON, or `.ino`) |
| **DHT11** | **Environmental Temp & Humidity** (`dht11_temp_c`, `dht11_humidity_pct`) | Digital (Single-Bus) | `D3` | Yes (UI, JSON, or `.ino`) |
| **YG1006** | **Infrared / Flame Detection** (`yg1006_flame`, `yg1006_ir_raw`) | Digital DO + Optional Analog AO | `D4` / `A1` | Yes (UI, JSON, or `.ino`) |
| **Smoke Sensor** | **Smoke / Gas Detection** (`smoke_raw_adc` 0–1023) | Analog ADC | `A0` | Yes (UI, JSON, or `.ino`) |

> **Updating Your Custom Pinout & Smoke Sensor Model**:
> You can update your exact Arduino Uno pin numbers, baud rate, serial port, and smoke sensor model directly in the **React Dashboard ("Custom Arduino Uno Pinout & Smoke Sensor Configuration" section)** or in `config/hardware_config.json`. Saving in the dashboard automatically updates both `config/hardware_config.json` and the `#define` macros inside `arduino/sensor_acquisition/sensor_acquisition.ino`.

---

## 4. Running the System

### A. Running on Ubuntu 22.04 with ROS 2 Humble
```bash
cd ros2_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install
source install/setup.bash

# Launch the Serial Acquisition, Data Logger, and ML Analytics ROS 2 nodes:
ros2 launch predictive_condition_monitoring condition_monitoring.launch.py serial_port:=/dev/ttyACM0 baud_rate:=115200
```

### B. Running the Python Processing + WebSocket Backend & React Dashboard
```bash
# 1. Start the Python Data Logger, ML Engine & WebSocket Server (Port 8000)
python3 backend/server.py

# 2. Start the React + Vite Dashboard (Port 5173)
cd dashboard
npm run dev
```

### C. Training & Evaluating the ML Framework via CLI
```bash
python3 -m ml_framework.train_and_evaluate
```
