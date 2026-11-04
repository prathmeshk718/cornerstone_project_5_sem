from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ConnectionStatus(str, Enum):
    CONNECTED = "Connected"
    NO_DATA = "No Data"
    DISCONNECTED = "Disconnected"


class HealthStatus(str, Enum):
    HEALTHY = "Healthy"
    WARNING = "Warning"
    MAINTENANCE_REQUIRED = "Maintenance Required"
    DISCONNECTED = "Disconnected"
    INSUFFICIENT_DATA = "Insufficient Data"


class EnvironmentalStatus(str, Enum):
    NORMAL = "Normal"
    ELEVATED_WATCH = "Elevated Thermal Watch"
    FIRE_HAZARD = "Potential Fire Detected"


class AlertDomain(str, Enum):
    CONNECTIVITY = "connectivity"
    SENSOR_HEALTH = "sensor_health"
    ENVIRONMENTAL = "environmental"


@dataclass
class AlertEvent:
    timestamp_ms: int
    timestamp_iso: str
    sensor_id: str
    sensor_name: str
    alert_domain: str       # "connectivity" | "sensor_health" | "environmental"
    alert_category: str     # e.g. "Arduino Disconnection", "Possible Stuck Sensor", "Potential Fire Detection"
    severity: str           # "info" | "warning" | "critical"
    title: str
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SensorEvaluation:
    sensor_id: str
    name: str
    unit: str
    display_value: float               # Always 0 when disconnected / missing data (Section 6)
    raw_value: Optional[float]         # None when missing/disconnected; 0.0 when genuinely 0
    is_valid: bool                     # Distinguishes genuine 0 from missing data
    connection_status: str             # Connected | No Data | Disconnected
    health_status: str                 # Healthy | Warning | Maintenance Required | Disconnected | Insufficient Data
    health_reasons: List[str] = field(default_factory=list)
    unchanged_duration_sec: float = 0.0
    last_step_delta: float = 0.0
    ml_anomaly_score: float = 0.0
    ml_condition_label: str = "Normal"
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
