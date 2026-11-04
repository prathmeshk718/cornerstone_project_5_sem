# Development of a Machine Learning Framework for Predictive Condition Monitoring and Maintenance Analytics

A modular, real-time multi-sensor monitoring, persistent telemetry storage, and predictive condition analytics framework built for **Arduino Uno**, **ROS 2 Humble** on **Ubuntu 22.04**, **Python (FastAPI + SQLite + scikit-learn)**, and a lightweight **React + Vite** dashboard.

---

## 1. End-to-End System Architecture

```text
Physical Sensors (DS18B20, Flame Module, DHT11)
        │
        ▼
   Arduino Uno (115200 baud JSON-over-Serial, 1 Hz heartbeat + explicit validity flags)
        │  USB Serial (/dev/ttyACM0 or /dev/ttyUSB0)
        ▼
  ROS 2 Humble (`serial_bridge` node on Ubuntu 22.04)
    ├─ /sensors/ds18b20/temperature  (sensor_msgs/msg/Temperature)
    ├─ /sensors/dht11/temperature    (sensor_msgs/msg/Temperature)   [separate measurement]
    ├─ /sensors/dht11/humidity       (sensor_msgs/msg/RelativeHumidity)
    ├─ /sensors/flame/detected       (std_msgs/msg/Bool)
    ├─ /sensors/flame/intensity      (std_msgs/msg/Float32)
    └─ /arduino/heartbeat            (std_msgs/msg/String)
        │
        ▼
  Python Condition Analytics & Storage (`analytics_server` / `MonitoringPipeline`)
    ├─ Heartbeat & Per-Sensor Timeout Supervisor
    ├─ Method 1: Constant Value (Stuck Sensor) Detector
    ├─ Method 2: Multi-Sensor Correlation & Cross-Validation (Fault vs. Fire Separation)
    ├─ Method 3: Sudden Change & Abnormal Variation Detector
    ├─ Hybrid ML Scorer: scikit-learn IsolationForest Multivariate Anomaly Model
    └─ Continuous SQLite Persistence (WAL mode, runs even when dashboard is closed)
        │
        ▼  WebSocket (`/ws/live`) & REST (`/api/*`)
  React + Vite Dashboard (Dynamic sensor selection, live charts, alerts, historical DB viewer)
```

---

## 2. Project Directory Layout

```text
├── arduino_firmware/
│   └── sensor_node_uno/
│       └── sensor_node_uno.ino              # Arduino Uno sketch (D2, D3, D4 + A0)
├── ros2_ws/
│   └── src/sensor_condition_monitoring/     # ROS 2 Humble ament_python package
│       ├── package.xml
│       ├── setup.py / setup.cfg
│       ├── config/ros2_params.yaml
│       ├── launch/monitoring_system.launch.py
│       └── sensor_condition_monitoring/
│           ├── serial_sensor_bridge_node.py # USB Serial -> ROS 2 topics
│           ├── ros2_analytics_node.py       # ROS 2 topics -> Python ML + SQLite + WS
│           └── mock_serial_publisher_node.py
├── backend/                                 # Core Python processing, ML, SQLite & WS server
│   ├── config.py                            # Sensor metadata & configurable thresholds
│   ├── models.py                            # Health, connectivity, environmental & alert types
│   ├── pipeline.py                          # Continuous 1 Hz collection & broadcast loop
│   ├── main.py                              # FastAPI + WebSocket (/ws/live) + REST API
│   ├── storage/database.py                  # Persistent WAL-mode SQLite database
│   ├── ingestion/
│   │   ├── simulator.py                     # Physics & fault-injection simulator
│   │   └── serial_reader.py                 # Optional direct USB serial adapter
│   └── analytics/
│       ├── constant_value.py                # Method 1: Stuck sensor detection
│       ├── cross_validation.py              # Method 2: Multi-sensor correlation & fire check
│       ├── sudden_change.py                 # Method 3: Abnormal jump / rate-of-change check
│       ├── ml_framework.py                  # scikit-learn IsolationForest condition scorer
│       └── engine.py                        # Unified evaluation & alert deduplication
├── dashboard/                               # Lightweight React + Vite frontend
│   ├── vite.config.js
│   └── src/
│       ├── App.jsx
│       └── components/
│           ├── SensorSelector.jsx
│           ├── SensorCard.jsx
│           ├── SimpleLineChart.jsx
│           ├── SensorInfoModal.jsx
│           ├── AlertsPanel.jsx
│           ├── HistoricalPanel.jsx
│           ├── ThresholdsModal.jsx
│           └── SimulatorToolbar.jsx
├── tests/
│   └── test_analytics_and_storage.py        # Automated pytest verification suite
└── scripts/
    ├── setup_ubuntu22_ros2_humble.sh
    └── run_system.sh
```

---

## 3. Hardware Wiring Guide (Arduino Uno)

| Physical Sensor | Signal / Pin | Arduino Uno Pin | Notes |
|---|---|---|---|
| **DS18B20 Temperature Probe** | `VDD` / `GND` / `DQ` | `5V` / `GND` / **`D2`** | Place a **4.7 kΩ pull-up resistor** between `DQ (D2)` and `5V`. |
| **DHT11 Temp & Humidity** | `VCC` / `GND` / `DATA` | `5V` / `GND` / **`D3`** | 3-pin PCB modules include an onboard 10 kΩ pull-up. |
| **Flame Sensor Module** | `VCC` / `GND` / `DO` / `AO` | `5V` / `GND` / **`D4`** & **`A0`** | `DO` is active-LOW (`LOW` = Flame Detected); `AO` provides 10-bit IR intensity. |

### Flashing the Arduino Uno
1. Open `arduino_firmware/sensor_node_uno/sensor_node_uno.ino` in Arduino IDE (on Ubuntu 22.04).
2. Install required libraries via **Tools → Manage Libraries**:
   - `OneWire` (Paul Stoffregen)
   - `DallasTemperature` (Miles Burton)
   - `DHT sensor library` + `Adafruit Unified Sensor` (Adafruit)
3. Select **Board: Arduino Uno**, **Port: `/dev/ttyACM0`** (or `/dev/ttyUSB0`), and click **Upload**.

---

## 4. Ubuntu 22.04 + ROS 2 Humble Deployment Instructions

### Step 1: Setup & Build
```bash
# Grant USB serial permission on Ubuntu 22.04 (log out/in once if newly added)
sudo usermod -aG dialout $USER

# Install Python/Node dependencies and build ROS 2 Humble package via colcon
./scripts/setup_ubuntu22_ros2_humble.sh
```

### Step 2: Launch with Real Arduino Uno + ROS 2 Humble
```bash
# Terminal 1: Launch ROS 2 Humble serial_bridge + analytics_server
source /opt/ros/humble/setup.bash
source ros2_ws/install/setup.bash
ros2 launch sensor_condition_monitoring monitoring_system.launch.py serial_port:=/dev/ttyACM0

# Terminal 2: Launch React + Vite dashboard
cd dashboard && npm run dev
```
Or run both with one command:
```bash
./scripts/run_system.sh ros2 /dev/ttyACM0
```

### Inspecting Live ROS 2 Humble Topics
```bash
source /opt/ros/humble/setup.bash
ros2 topic list
ros2 topic echo /sensors/ds18b20/temperature
ros2 topic echo /sensors/dht11/temperature
ros2 topic echo /sensors/dht11/humidity
ros2 topic echo /sensors/flame/detected
ros2 topic echo /analytics/sensor_health
```

### Running in Built-in Hardware & Fault Simulator Mode (No Hardware Needed)
```bash
./scripts/run_system.sh simulator
```

---

## 5. Condition Monitoring, Disconnection Handling & ML Analytics

### Disconnection & Genuine-Zero Separation (Section 6 & 7)
- **Live Display**: Whenever Arduino Uno disconnects or an individual sensor stops transmitting valid data past its configurable timeout (`arduino_timeout_sec` / `sensor_timeout_sec`), its displayed value immediately becomes **`0`** with status **`Disconnected`** or **`No Data`** (never showing stale historical values as current), while all remaining healthy sensors continue streaming.
- **Database Storage (`backend/data/sensor_monitoring.db`)**:
  - **Missing / Disconnected reading**: Stored as SQL `NULL` with `*_valid = 0`.
  - **Genuine `0` measurement** (e.g., DS18B20 in a `0.0°C` ice bath or Flame Sensor `0 = No Flame`): Stored as numeric `0.0` / `0` with `*_valid = 1` and `Connected` status.

### Sensor Health & Maintenance Methods (Section 8)
1. **Method 1 — Constant Value Detection (`constant_value.py`)**:
   - Tracks unchanged duration using sensor-specific quantization tolerances and observation windows (`Warning` → `Maintenance Required`: *"Sensor Health: Possible Maintenance Required"*).
   - Accounts for legitimate steady `0` (OFF) states on digital flame sensors and cross-checks peer sensor movement to avoid false alarms in stable rooms.
2. **Method 2 — Multi-Sensor Correlation & Cross-Validation (`cross_validation.py`)**:
   - **Uncorroborated Flame (`Flame=1` while DS18B20 & DHT11 stay ambient)**: Flags *"Possible Flame Sensor Fault – Sensor Readings Inconsistent"* without raising a false fire alarm.
   - **Corroborated Fire (`Flame=1` + thermal rise on DS18B20/DHT11, with optional humidity drop)**: Raises a critical **Environmental Fire Hazard Alert** while keeping functioning sensors **`Healthy`**.
   - **DS18B20 vs. DHT11 Divergence**: Flags sustained cross-sensor temperature disagreement beyond configurable tolerance.
3. **Method 3 — Sudden Change & Abnormal Variation (`sudden_change.py`)**:
   - Distinguishes gradual ramps (`30°C → 31°C → 32°C → 33°C → 34°C`) from physically implausible jumps (`34°C → 95°C` or DS18B20 `85°C` reset spikes), generating *"Abnormal Sensor Variation – Inspection Recommended"*.
4. **Hybrid Machine Learning Scorer (`ml_framework.py`)**:
   - Uses a `scikit-learn` **`IsolationForest`** + `StandardScaler` over a 7-feature rolling multivariate window (rolling std, step slew rates, DS18B20–DHT11 thermal residual, and flame-thermal discordance) to compute continuous predictive maintenance risk scores (`0–100%`).

---

## 6. Running Automated Tests

```bash
PYTHONPATH=. pytest -v tests/test_analytics_and_storage.py
```
