import tempfile
from pathlib import Path

from backend.config import ThresholdsConfig
from backend.pipeline import MonitoringPipeline
from backend.storage.database import SensorDatabase


def _make_frame(
    seq: int,
    ds_temp=28.5,
    ds_ok=True,
    dht_temp=29.0,
    dht_hum=54.0,
    dht_ok=True,
    flame_det=0,
    flame_int=6.0,
    flame_ok=True,
):
    return {
        "seq": seq,
        "port": "/dev/ttyACM0",
        "ds18b20": {"ok": ds_ok, "temp_c": ds_temp if ds_ok else None},
        "dht11": {
            "ok": dht_ok,
            "temp_c": dht_temp if dht_ok else None,
            "humidity_pct": dht_hum if dht_ok else None,
        },
        "flame": {
            "ok": flame_ok,
            "detected": flame_det if flame_ok else None,
            "analog_raw": 960 if flame_ok else None,
            "intensity_pct": flame_int if flame_ok else None,
        },
    }


def _new_pipeline(tmpdir: str, **threshold_overrides) -> MonitoringPipeline:
    db_path = str(Path(tmpdir) / "test.db")
    cfg_path = str(Path(tmpdir) / "thresholds.json")
    p = MonitoringPipeline(mode="ros2", db_path=db_path, thresholds_path=cfg_path)
    if threshold_overrides:
        p.update_thresholds(threshold_overrides)
    return p


def test_warmup_disconnect_and_genuine_zero():
    with tempfile.TemporaryDirectory() as tmp:
        p = _new_pipeline(tmp, min_samples_for_health=4, arduino_timeout_sec=3.0, sensor_timeout_sec=3.0)

        # First 3 samples -> Insufficient Data
        for i in range(1, 4):
            t = float(i)
            p.ingest_arduino_frame(
                _make_frame(i, ds_temp=28.0 + 0.1 * i, dht_temp=28.5 + 0.1 * i), now_mono=t
            )
            snap, _ = p.step_once(now_mono=t, ts_ms=i * 1000)
            assert snap["sensors"]["ds18b20_temp"]["health_status"] == "Insufficient Data"

        # 4th sample -> Healthy
        p.ingest_arduino_frame(_make_frame(4, ds_temp=28.5, dht_temp=29.0), now_mono=4.0)
        snap, _ = p.step_once(now_mono=4.0, ts_ms=4000)
        assert snap["sensors"]["ds18b20_temp"]["health_status"] == "Healthy"
        assert snap["sensors"]["flame_sensor"]["health_status"] == "Healthy"

        # Single sensor failure: DS18B20 unplugged, DHT11 & Flame remain online
        p.ingest_arduino_frame(_make_frame(5, ds_ok=False, dht_temp=29.1, dht_hum=53.5), now_mono=5.0)
        snap, alerts = p.step_once(now_mono=5.0, ts_ms=5000)
        assert snap["sensors"]["ds18b20_temp"]["display_value"] == 0.0
        assert snap["sensors"]["ds18b20_temp"]["raw_value"] is None
        assert snap["sensors"]["ds18b20_temp"]["is_valid"] is False
        assert snap["sensors"]["ds18b20_temp"]["connection_status"] == "No Data"
        assert snap["sensors"]["ds18b20_temp"]["health_status"] == "Disconnected"
        # Other sensors keep working normally
        assert snap["sensors"]["dht11_temp"]["connection_status"] == "Connected"
        assert snap["sensors"]["dht11_temp"]["display_value"] == 29.1
        assert any(a["alert_category"] == "Sensor Disconnection" for a in alerts)

        # Genuine 0.0°C ice-bath measurement on DS18B20 must NOT be treated as missing!
        for step_idx in range(6, 11):
            t = float(step_idx)
            p.ingest_arduino_frame(
                _make_frame(step_idx, ds_temp=0.0, ds_ok=True, dht_temp=29.0), now_mono=t
            )
            snap, _ = p.step_once(now_mono=t, ts_ms=step_idx * 1000)
        assert snap["sensors"]["ds18b20_temp"]["display_value"] == 0.0
        assert snap["sensors"]["ds18b20_temp"]["raw_value"] == 0.0
        assert snap["sensors"]["ds18b20_temp"]["is_valid"] is True
        assert snap["sensors"]["ds18b20_temp"]["connection_status"] == "Connected"

        # Full Arduino USB disconnection timeout (advance clock > 3.0s without frames)
        snap, alerts = p.step_once(now_mono=15.0, ts_ms=15000)
        assert snap["arduino_connected"] is False
        for sid in ("ds18b20_temp", "dht11_temp", "dht11_humidity", "flame_sensor"):
            assert snap["sensors"][sid]["display_value"] == 0
            assert snap["sensors"][sid]["connection_status"] == "Disconnected"
            assert snap["sensors"][sid]["health_status"] == "Disconnected"
        assert any(a["alert_category"] == "Arduino Disconnection" for a in alerts)

        # Verify SQLite persistence and distinction between missing vs genuine 0
        hist = p.db.query_history(limit=100)
        assert hist["missing_counts"]["ds18b20_temp"] == 2  # step 5 + step 15
        assert hist["genuine_zero_counts"]["ds18b20_temp"] == 5  # steps 6..10

        db_file = p.db.db_path
        p.db.close()
        reopened = SensorDatabase(db_file)
        stats = reopened.get_storage_stats()
        assert stats["total_readings"] == 11
        reopened.close()


def test_method1_constant_value_and_flame_off_immunity():
    with tempfile.TemporaryDirectory() as tmp:
        p = _new_pipeline(
            tmp,
            min_samples_for_health=4,
            ds18b20_stuck_warn_sec=6.0,
            ds18b20_stuck_maint_sec=12.0,
        )
        seen_maint_alert = False
        for i in range(1, 18):
            t = float(i)
            # DS18B20 stays completely frozen at 27.50°C while DHT11 varies slightly & Flame stays 0
            p.ingest_arduino_frame(
                _make_frame(
                    i,
                    ds_temp=27.50,
                    dht_temp=28.0 + (i % 3) * 0.4,
                    dht_hum=52.0 + (i % 2) * 1.0,
                    flame_det=0,
                    flame_int=6.0 + (i % 3) * 0.3,
                ),
                now_mono=t,
            )
            snap, alerts = p.step_once(now_mono=t, ts_ms=i * 1000)
            if any(a["title"] == "Sensor Health: Possible Maintenance Required" for a in alerts):
                seen_maint_alert = True

        assert seen_maint_alert is True
        assert snap["sensors"]["ds18b20_temp"]["health_status"] == "Maintenance Required"
        # Digital Flame sensor staying 0 must remain Healthy!
        assert snap["sensors"]["flame_sensor"]["health_status"] == "Healthy"


def test_method2_inconsistent_flame_vs_genuine_fire():
    with tempfile.TemporaryDirectory() as tmp:
        p = _new_pipeline(
            tmp,
            min_samples_for_health=4,
            flame_validation_window_sec=3.0,
            flame_fault_maint_sec=6.0,
            fire_temp_rise_threshold_c=2.0,
        )
        # Establish ambient baseline (~28.5°C, 55% RH)
        for i in range(1, 7):
            t = float(i)
            p.ingest_arduino_frame(
                _make_frame(i, ds_temp=28.5 + (i % 2) * 0.1, dht_temp=29.0, dht_hum=55.0, flame_det=0),
                now_mono=t,
            )
            p.step_once(now_mono=t, ts_ms=i * 1000)

        # Scenario A: Flame reports ON continuously, but temperatures do NOT increase
        inconsistent_alert_seen = False
        for i in range(7, 16):
            t = float(i)
            p.ingest_arduino_frame(
                _make_frame(i, ds_temp=28.6, dht_temp=29.0, dht_hum=55.0, flame_det=1, flame_int=80.0),
                now_mono=t,
            )
            snap, alerts = p.step_once(now_mono=t, ts_ms=i * 1000)
            if any(
                a["title"] == "Possible Flame Sensor Fault – Sensor Readings Inconsistent"
                for a in alerts
            ):
                inconsistent_alert_seen = True

        assert inconsistent_alert_seen is True
        assert snap["sensors"]["flame_sensor"]["health_status"] == "Maintenance Required"
        assert snap["environmental"]["is_fire_alert"] is False

        # Return briefly to normal, then test Scenario B: Genuine Fire!
        for i in range(16, 20):
            t = float(i)
            p.ingest_arduino_frame(
                _make_frame(i, ds_temp=28.6, dht_temp=29.0, dht_hum=55.0, flame_det=0, flame_int=6.0),
                now_mono=t,
            )
            p.step_once(now_mono=t, ts_ms=i * 1000)

        fire_alert_seen = False
        for idx, i in enumerate(range(20, 27), start=1):
            t = float(i)
            p.ingest_arduino_frame(
                _make_frame(
                    i,
                    ds_temp=28.6 + idx * 2.2,
                    dht_temp=29.0 + idx * 1.9,
                    dht_hum=55.0 - idx * 1.5,
                    flame_det=1,
                    flame_int=88.0,
                ),
                now_mono=t,
            )
            snap, alerts = p.step_once(now_mono=t, ts_ms=i * 1000)
            if any(a["alert_domain"] == "environmental" for a in alerts):
                fire_alert_seen = True

        assert fire_alert_seen is True
        assert snap["environmental"]["is_fire_alert"] is True
        assert snap["environmental"]["status"] == "Potential Fire Detected"
        # All hardware sensors must remain Healthy during a corroborated real fire!
        for sid in ("ds18b20_temp", "dht11_temp", "dht11_humidity", "flame_sensor"):
            assert snap["sensors"][sid]["health_status"] == "Healthy"


def test_method3_gradual_vs_sudden_jump():
    with tempfile.TemporaryDirectory() as tmp:
        p = _new_pipeline(tmp, min_samples_for_health=4, ds18b20_max_step_c=6.0)

        # Gradual ramp: 30 -> 31 -> 32 -> 33 -> 34°C (must remain Healthy!)
        for idx, temp in enumerate([30.0, 31.0, 32.0, 33.0, 34.0], start=1):
            t = float(idx)
            p.ingest_arduino_frame(
                _make_frame(idx, ds_temp=temp, dht_temp=temp + 0.2, dht_hum=50.0),
                now_mono=t,
            )
            snap, _ = p.step_once(now_mono=t, ts_ms=idx * 1000)

        assert snap["sensors"]["ds18b20_temp"]["health_status"] == "Healthy"

        # Sudden implausible jump: 34°C -> 95°C while peer stays ~34.2°C!
        p.ingest_arduino_frame(
            _make_frame(6, ds_temp=95.0, dht_temp=34.2, dht_hum=50.0),
            now_mono=6.0,
        )
        snap, alerts = p.step_once(now_mono=6.0, ts_ms=6000)
        assert snap["sensors"]["ds18b20_temp"]["health_status"] in ("Warning", "Maintenance Required")
        assert any(
            a["title"] == "Abnormal Sensor Variation – Inspection Recommended" for a in alerts
        )
