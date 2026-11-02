#!/usr/bin/env python3
"""
CLI Runner for training and evaluating the Predictive Condition Monitoring
Machine Learning Framework on the logged sensor CSV dataset.
"""

import json
import os
import sys

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from data_pipeline.sensor_logger import SensorDataLogger
from data_pipeline.serial_and_sim_source import seed_initial_historical_dataset
from ml_framework.pipeline import PredictiveConditionMonitoringFramework


def main():
    csv_path = os.path.join(WORKSPACE_ROOT, "data/sensor_readings_history.csv")
    sqlite_path = os.path.join(WORKSPACE_ROOT, "data/condition_monitoring.db")
    session_dir = os.path.join(WORKSPACE_ROOT, "data/logged_sessions")
    model_dir = os.path.join(WORKSPACE_ROOT, "data/models")

    logger = SensorDataLogger(
        csv_path=csv_path, sqlite_path=sqlite_path, session_dir=session_dir
    )
    seed_initial_historical_dataset(logger, num_samples=300)

    framework = PredictiveConditionMonitoringFramework(model_dir=model_dir)
    report = framework.train_and_evaluate(csv_path)

    print("=" * 72)
    print("MACHINE LEARNING FRAMEWORK EVALUATION REPORT")
    print("=" * 72)
    print(f"Dataset Samples : {report['dataset_total_samples']} (Train: {report['train_samples']}, Test: {report['test_samples']})")
    metrics = report["metrics"]
    print(f"Accuracy        : {metrics['accuracy'] * 100:.2f}%")
    print(f"Precision (Mac) : {metrics['precision_macro'] * 100:.2f}%")
    print(f"Recall (Mac)    : {metrics['recall_macro'] * 100:.2f}%")
    print(f"F1-Score (Mac)  : {metrics['f1_score_macro'] * 100:.2f}%")
    print("-" * 72)
    print("Per-Class Performance:")
    for cls in report["per_class_metrics"]:
        print(
            f"  [{cls['class_id']}] {cls['class_name']:<42} | "
            f"P: {cls['precision']:.3f} | R: {cls['recall']:.3f} | F1: {cls['f1_score']:.3f} (n={cls['test_support']})"
        )
    print("-" * 72)
    print("Confusion Matrix (Rows = Actual, Cols = Predicted):")
    for row_idx, row in enumerate(report["confusion_matrix"]["matrix"]):
        print(f"  Class {row_idx}: {row}")
    print("=" * 72)


if __name__ == "__main__":
    main()
