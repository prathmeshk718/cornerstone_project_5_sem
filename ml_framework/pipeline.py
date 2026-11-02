from collections import deque
import json
import os
import time
from typing import Dict, Any, List, Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    confusion_matrix,
)
from sklearn.model_selection import train_test_split

from ml_framework.preprocessing import SensorDataPreprocessor, FEATURE_COLUMNS
from data_pipeline.sensor_logger import LABEL_MAP


class PredictiveConditionMonitoringFramework:
    """
    Integrated Machine Learning Framework & Maintenance Analytics Engine.
    Covers Sections 6 (A-F) and Section 7 of the Project Specification:
      - Data Preprocessing (Cleaning, Imputation, Smoothing, Feature Extraction, Scaling)
      - Condition Monitoring (Independent sensor status & Equipment Health Index)
      - Anomaly Detection (Unsupervised Isolation Forest)
      - Fault Detection (Supervised Random Forest Multi-Class Classifier)
      - Predictive Maintenance (Rolling trend projection & Time-to-Threshold forecasting)
      - Model Evaluation (Accuracy, Precision, Recall, F1-Score, Confusion Matrix)
      - Maintenance Analytics (Risk Level, Warnings, Suggested Maintenance Actions)
    """

    def __init__(self, model_dir: str = "data/models"):
        self.model_dir = model_dir
        os.makedirs(self.model_dir, exist_ok=True)

        self.preprocessor = SensorDataPreprocessor(window_size=12, ema_alpha=0.35)
        self.anomaly_detector = IsolationForest(
            n_estimators=120,
            contamination=0.10,
            random_state=42,
        )
        self.fault_classifier = RandomForestClassifier(
            n_estimators=150,
            max_depth=10,
            min_samples_leaf=2,
            class_weight="balanced",
            random_state=42,
        )

        self.models_trained: bool = False
        self.evaluation_report: Dict[str, Any] = {}
        self._trend_history: deque = deque(maxlen=30)

    def train_and_evaluate(self, csv_path: str) -> Dict[str, Any]:
        """
        Loads the historical CSV sensor dataset, executes the full preprocessing
        pipeline, trains both the Unsupervised Anomaly Detector (Isolation Forest)
        and Supervised Fault Classifier (Random Forest), and computes comprehensive
        evaluation metrics (Accuracy, Precision, Recall, F1-score, Confusion Matrix).
        """
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"Dataset CSV not found at {csv_path}")

        df = pd.read_csv(csv_path)
        if len(df) < 20:
            raise ValueError(
                f"Need at least 20 logged sensor rows to train ML models (found {len(df)})."
            )

        X_scaled, y, df_feat = self.preprocessor.prepare_training_dataset(
            df, target_col="operator_label"
        )

        # 1. Train Isolation Forest on Normal Operating Samples (class 0)
        normal_mask = y == 0
        X_normal = X_scaled[normal_mask] if np.sum(normal_mask) >= 10 else X_scaled
        self.anomaly_detector.fit(X_normal)

        # 2. Train & Evaluate Supervised Fault Detection Model
        unique_classes, counts = np.unique(y, return_counts=True)
        can_stratify = len(unique_classes) > 1 and np.min(counts) >= 2

        if len(df) >= 40 and len(unique_classes) > 1:
            X_train, X_test, y_train, y_test = train_test_split(
                X_scaled,
                y,
                test_size=0.25,
                random_state=42,
                stratify=y if can_stratify else None,
            )
        else:
            X_train, X_test, y_train, y_test = X_scaled, X_scaled, y, y

        self.fault_classifier.fit(X_train, y_train)
        y_pred = self.fault_classifier.predict(X_test)

        # Compute Model Evaluation Metrics (Section 6.F)
        acc = float(accuracy_score(y_test, y_pred))
        prec_macro = float(
            precision_score(y_test, y_pred, average="macro", zero_division=0)
        )
        rec_macro = float(
            recall_score(y_test, y_pred, average="macro", zero_division=0)
        )
        f1_macro = float(
            f1_score(y_test, y_pred, average="macro", zero_division=0)
        )
        prec_weighted = float(
            precision_score(y_test, y_pred, average="weighted", zero_division=0)
        )
        rec_weighted = float(
            recall_score(y_test, y_pred, average="weighted", zero_division=0)
        )
        f1_weighted = float(
            f1_score(y_test, y_pred, average="weighted", zero_division=0)
        )

        all_label_ids = sorted(list(LABEL_MAP.keys()))
        cm = confusion_matrix(y_test, y_pred, labels=all_label_ids).tolist()

        # Per-class metrics
        prec_per_class = precision_score(
            y_test, y_pred, labels=all_label_ids, average=None, zero_division=0
        )
        rec_per_class = recall_score(
            y_test, y_pred, labels=all_label_ids, average=None, zero_division=0
        )
        f1_per_class = f1_score(
            y_test, y_pred, labels=all_label_ids, average=None, zero_division=0
        )

        per_class_report = []
        for idx, cls_id in enumerate(all_label_ids):
            support = int(np.sum(y_test == cls_id))
            per_class_report.append(
                {
                    "class_id": cls_id,
                    "class_name": LABEL_MAP[cls_id],
                    "precision": round(float(prec_per_class[idx]), 4),
                    "recall": round(float(rec_per_class[idx]), 4),
                    "f1_score": round(float(f1_per_class[idx]), 4),
                    "test_support": support,
                }
            )

        importances = self.fault_classifier.feature_importances_
        feature_importance_list = [
            {
                "feature": col,
                "importance": round(float(imp), 4),
            }
            for col, imp in sorted(
                zip(FEATURE_COLUMNS, importances),
                key=lambda x: x[1],
                reverse=True,
            )
        ]

        # Retrain on 100% of available data after evaluation split so live inference uses all samples
        self.fault_classifier.fit(X_scaled, y)
        self.models_trained = True

        self.evaluation_report = {
            "trained_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "dataset_total_samples": int(len(df)),
            "train_samples": int(len(X_train)),
            "test_samples": int(len(X_test)),
            "models": {
                "anomaly_detection": "Isolation Forest (n_estimators=120)",
                "fault_classification": "Random Forest Classifier (n_estimators=150)",
                "predictive_trend": "Rolling Linear Trend Forecaster",
            },
            "metrics": {
                "accuracy": round(acc, 4),
                "precision_macro": round(prec_macro, 4),
                "recall_macro": round(rec_macro, 4),
                "f1_score_macro": round(f1_macro, 4),
                "precision_weighted": round(prec_weighted, 4),
                "recall_weighted": round(rec_weighted, 4),
                "f1_score_weighted": round(f1_weighted, 4),
            },
            "confusion_matrix": {
                "labels": [LABEL_MAP[i] for i in all_label_ids],
                "label_ids": all_label_ids,
                "matrix": cm,
            },
            "per_class_metrics": per_class_report,
            "feature_importances": feature_importance_list,
        }

        # Persist models & evaluation report
        joblib.dump(
            {
                "preprocessor": self.preprocessor,
                "anomaly_detector": self.anomaly_detector,
                "fault_classifier": self.fault_classifier,
                "evaluation_report": self.evaluation_report,
            },
            os.path.join(self.model_dir, "predictive_monitoring_bundle.joblib"),
        )
        with open(
            os.path.join(self.model_dir, "evaluation_metrics.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(self.evaluation_report, f, indent=2)

        return self.evaluation_report

    def analyze_realtime_frame(
        self, raw_frame: Dict[str, Any], thresholds: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Executes real-time Condition Monitoring, Anomaly Detection, Fault Detection,
        Predictive Maintenance Trend Forecasting, and Maintenance Analytics on an
        incoming sensor reading.
        """
        features, vec_scaled = self.preprocessor.process_realtime_sample(raw_frame)
        now = float(raw_frame.get("timestamp_unix", time.time()))
        self._trend_history.append(
            {
                "ts": now,
                "ds18b20_temp_c": features["ds18b20_temp_c"],
                "dht11_temp_c": features["dht11_temp_c"],
                "dht11_humidity_pct": features["dht11_humidity_pct"],
                "smoke_raw_adc": features["smoke_raw_adc"],
                "temp_differential_c": features["temp_differential_c"],
            }
        )

        # --------------------------------------------------------------------
        # 1. Per-Sensor Condition Assessment (Independent Sensors)
        # --------------------------------------------------------------------
        ds_temp = features["ds18b20_temp_c"]
        dht_temp = features["dht11_temp_c"]
        dht_hum = features["dht11_humidity_pct"]
        yg_flame = int(features["yg1006_flame"])
        smoke_adc = int(features["smoke_raw_adc"])
        temp_diff = features["temp_differential_c"]

        # DS18B20 Equipment Temperature Status
        if ds_temp >= 65.0:
            ds_status = "CRITICAL"
        elif ds_temp >= 54.0:
            ds_status = "WARNING"
        else:
            ds_status = "NORMAL"

        # DHT11 Environmental Temperature & Humidity Status
        if dht_temp >= 40.0 or dht_hum >= 80.0:
            dht_status = "CRITICAL"
        elif dht_temp >= 35.0 or dht_hum >= 70.0 or dht_hum <= 22.0:
            dht_status = "WARNING"
        else:
            dht_status = "NORMAL"

        # YG1006 Flame / Infrared Status
        yg_status = "CRITICAL" if yg_flame == 1 else "NORMAL"

        # Smoke Sensor Status
        if smoke_adc >= 460:
            smoke_status = "CRITICAL"
        elif smoke_adc >= 300:
            smoke_status = "WARNING"
        else:
            smoke_status = "NORMAL"

        sensor_statuses = {
            "ds18b20": ds_status,
            "dht11": dht_status,
            "yg1006": yg_status,
            "smoke": smoke_status,
        }

        # --------------------------------------------------------------------
        # 2. ML Anomaly Detection (Isolation Forest) & Fault Detection (RF)
        # --------------------------------------------------------------------
        is_ml_anomaly = False
        anomaly_score = 0.0
        predicted_class_id = 0
        confidence = 0.95

        if self.models_trained:
            iso_pred = int(self.anomaly_detector.predict(vec_scaled)[0])  # -1 = anomaly, 1 = normal
            raw_score = float(self.anomaly_detector.decision_function(vec_scaled)[0])
            anomaly_score = round(raw_score, 4)
            is_ml_anomaly = iso_pred == -1

            probs = self.fault_classifier.predict_proba(vec_scaled)[0]
            classes = self.fault_classifier.classes_
            best_idx = int(np.argmax(probs))
            predicted_class_id = int(classes[best_idx])
            confidence = round(float(probs[best_idx]), 3)

        # Ensure deterministic safety overrides for physical critical states
        if yg_flame == 1:
            predicted_class_id = 4
            is_ml_anomaly = True
            confidence = max(confidence, 0.99)
        elif smoke_status == "CRITICAL" and predicted_class_id == 0:
            predicted_class_id = 3
            is_ml_anomaly = True
        elif ds_status == "CRITICAL" and predicted_class_id == 0:
            predicted_class_id = 1
            is_ml_anomaly = True
        elif dht_status == "CRITICAL" and predicted_class_id == 0:
            predicted_class_id = 2
            is_ml_anomaly = True

        any_sensor_abnormal = any(
            s in ("WARNING", "CRITICAL") for s in sensor_statuses.values()
        )
        is_anomaly = bool(is_ml_anomaly or any_sensor_abnormal or predicted_class_id != 0)
        predicted_label = LABEL_MAP.get(
            predicted_class_id, "Normal Operating Condition"
        )

        # --------------------------------------------------------------------
        # 3. Predictive Maintenance Trend Analysis (Section 6.E)
        # --------------------------------------------------------------------
        trend_info = self._compute_predictive_trends()

        # --------------------------------------------------------------------
        # 4. Maintenance Analytics & Actionable Recommendations (Section 7)
        # --------------------------------------------------------------------
        analytics = self._build_maintenance_analytics(
            features=features,
            sensor_statuses=sensor_statuses,
            predicted_class_id=predicted_class_id,
            predicted_label=predicted_label,
            confidence=confidence,
            is_anomaly=is_anomaly,
            anomaly_score=anomaly_score,
            trend_info=trend_info,
        )

        return {
            "preprocessed_features": features,
            "sensor_statuses": sensor_statuses,
            "is_anomaly": is_anomaly,
            "anomaly_score": anomaly_score,
            "predicted_class_id": predicted_class_id,
            "predicted_label": predicted_label,
            "prediction_confidence": confidence,
            "predictive_trends": trend_info,
            **analytics,
        }

    def _compute_predictive_trends(self) -> Dict[str, Any]:
        """
        Calculates rate-of-change slopes on recent sensor history to predict
        potential threshold crossings before equipment failure occurs.
        """
        if len(self._trend_history) < 5:
            return {
                "ds18b20_slope_c_per_min": 0.0,
                "smoke_slope_adc_per_min": 0.0,
                "estimated_minutes_to_temp_warning": None,
                "estimated_minutes_to_smoke_warning": None,
                "trend_status": "STABLE",
            }

        pts = list(self._trend_history)
        t0 = pts[0]["ts"]
        times_min = np.array([max(0.001, (p["ts"] - t0) / 60.0) for p in pts])
        ds_temps = np.array([p["ds18b20_temp_c"] for p in pts])
        smokes = np.array([p["smoke_raw_adc"] for p in pts])

        if (times_min[-1] - times_min[0]) < 0.02:
            ds_slope = 0.0
            smoke_slope = 0.0
        else:
            ds_slope = float(np.polyfit(times_min, ds_temps, 1)[0])
            smoke_slope = float(np.polyfit(times_min, smokes, 1)[0])

        curr_ds = float(ds_temps[-1])
        curr_smoke = float(smokes[-1])

        est_temp_min: Optional[float] = None
        if ds_slope > 0.8 and curr_ds < 55.0:
            est_temp_min = round((55.0 - curr_ds) / ds_slope, 1)

        est_smoke_min: Optional[float] = None
        if smoke_slope > 15.0 and curr_smoke < 320.0:
            est_smoke_min = round((320.0 - curr_smoke) / smoke_slope, 1)

        trend_status = "STABLE"
        if ds_slope > 1.5 or smoke_slope > 25.0:
            trend_status = "RISING_DEGRADATION_TREND"

        return {
            "ds18b20_slope_c_per_min": round(ds_slope, 2),
            "smoke_slope_adc_per_min": round(smoke_slope, 1),
            "estimated_minutes_to_temp_warning": est_temp_min,
            "estimated_minutes_to_smoke_warning": est_smoke_min,
            "trend_status": trend_status,
        }

    def _build_maintenance_analytics(
        self,
        features: Dict[str, float],
        sensor_statuses: Dict[str, str],
        predicted_class_id: int,
        predicted_label: str,
        confidence: float,
        is_anomaly: bool,
        anomaly_score: float,
        trend_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Implements Section 7 (Maintenance Analytics):
        Converts sensor readings and ML predictions into understandable
        maintenance information.
        """
        ds_temp = features["ds18b20_temp_c"]
        dht_temp = features["dht11_temp_c"]
        dht_hum = features["dht11_humidity_pct"]
        yg_flame = int(features["yg1006_flame"])
        smoke_adc = int(features["smoke_raw_adc"])
        temp_diff = features["temp_differential_c"]

        fault_alerts: List[str] = []
        maintenance_warnings: List[str] = []
        suggested_actions: List[str] = []

        # Determine Risk Level & Operating Condition
        if yg_flame == 1 or predicted_class_id == 4:
            operating_condition = "Potential Fault Alert"
            risk_level = "CRITICAL"
            health_index = 12
        elif (
            "CRITICAL" in sensor_statuses.values()
            or predicted_class_id in (1, 3)
        ):
            operating_condition = "Potential Fault Alert"
            risk_level = "HIGH"
            health_index = 38
        elif (
            "WARNING" in sensor_statuses.values()
            or predicted_class_id == 2
            or is_anomaly
            or trend_info["trend_status"] == "RISING_DEGRADATION_TREND"
        ):
            operating_condition = "Abnormal Operating Condition"
            risk_level = "MODERATE"
            health_index = 68
        else:
            operating_condition = "Normal Operating Condition"
            risk_level = "LOW"
            health_index = 96

        # Build specific Fault Alerts, Warnings, and Suggested Maintenance Actions
        if yg_flame == 1 or predicted_class_id == 4:
            fault_alerts.append(
                "CRITICAL FLAME / INFRARED RADIATION DETECTED by YG1006 Phototransistor."
            )
            maintenance_warnings.append(
                "Immediate fire/ignition hazard detected near monitored equipment."
            )
            suggested_actions.extend(
                [
                    "Immediately de-energize equipment power supply and trigger fire safety protocol.",
                    "Inspect electrical terminals, motor windings, and surrounding enclosure for arcing or flame.",
                    "Verify YG1006 sensor lens cleanliness and wiring after area is confirmed safe.",
                ]
            )

        if sensor_statuses["ds18b20"] in ("WARNING", "CRITICAL") or predicted_class_id == 1:
            fault_alerts.append(
                f"Equipment Thermal Overload: DS18B20 reading {ds_temp:.1f}°C (Thermal ΔT = +{temp_diff:.1f}°C above ambient)."
            )
            maintenance_warnings.append(
                "Equipment surface temperature exceeds normal operating envelope while ambient temperature remains lower, indicating internal heat generation."
            )
            suggested_actions.extend(
                [
                    "Inspect equipment cooling fan, heat sink fins, and airflow pathways for blockage.",
                    "Check bearing lubrication, shaft alignment, and motor current draw for mechanical overload.",
                    "Schedule thermal inspection before insulation or component degradation occurs.",
                ]
            )

        if sensor_statuses["dht11"] in ("WARNING", "CRITICAL") or predicted_class_id == 2:
            fault_alerts.append(
                f"Environmental Condition Anomaly: DHT11 Ambient Temp = {dht_temp:.1f}°C, Relative Humidity = {dht_hum:.1f}%."
            )
            maintenance_warnings.append(
                "Surrounding room temperature or humidity is outside recommended operating conditions, risking condensation or reduced heat dissipation."
            )
            suggested_actions.extend(
                [
                    "Check room HVAC, ventilation exhaust, and dehumidification systems.",
                    "Inspect electrical cabinet seals and moisture barriers to prevent condensation corrosion.",
                ]
            )

        if sensor_statuses["smoke"] in ("WARNING", "CRITICAL") or predicted_class_id == 3:
            fault_alerts.append(
                f"Smoke / Gas Emission Alert: Analog Smoke Sensor reading {smoke_adc} ADC (Baseline ~160 ADC)."
            )
            maintenance_warnings.append(
                "Elevated smoke or volatile gas concentration detected around equipment."
            )
            suggested_actions.extend(
                [
                    "Inspect wiring insulation, capacitors, and belt drives for early smoldering or thermal breakdown.",
                    "Ensure enclosure ventilation is clear and verify smoke sensor calibration.",
                ]
            )

        # Predictive trend warnings (early warning before threshold breach)
        if trend_info["trend_status"] == "RISING_DEGRADATION_TREND":
            if trend_info["ds18b20_slope_c_per_min"] > 1.2:
                eta = trend_info["estimated_minutes_to_temp_warning"]
                eta_str = f" (est. {eta} min to warning threshold)" if eta else ""
                maintenance_warnings.append(
                    f"Predictive Trend Warning: Equipment temperature rising at +{trend_info['ds18b20_slope_c_per_min']:.1f}°C/min{eta_str}."
                )
                if not suggested_actions:
                    suggested_actions.append(
                        "Perform preventive inspection of cooling and load conditions to arrest rising thermal trend."
                    )
            if trend_info["smoke_slope_adc_per_min"] > 20.0:
                maintenance_warnings.append(
                    f"Predictive Trend Warning: Smoke analog reading drifting upward (+{trend_info['smoke_slope_adc_per_min']:.0f} ADC/min)."
                )

        if not fault_alerts:
            fault_alerts.append("No active equipment or environmental faults detected.")
        if not maintenance_warnings:
            maintenance_warnings.append(
                "All sensor readings (DS18B20, DHT11, YG1006, Smoke) are within normal operating baselines."
            )
        if not suggested_actions:
            suggested_actions.append(
                "Continue routine condition monitoring and scheduled preventive logging."
            )

        return {
            "operating_condition": operating_condition,
            "risk_level": risk_level,
            "health_index": health_index,
            "fault_alerts": fault_alerts,
            "maintenance_warnings": maintenance_warnings,
            "suggested_actions": suggested_actions,
        }
