import React, { useCallback, useEffect, useRef, useState } from 'react';
import AlertsPanel from './components/AlertsPanel';
import HistoricalPanel from './components/HistoricalPanel';
import SensorCard from './components/SensorCard';
import SensorInfoModal from './components/SensorInfoModal';
import SensorSelector from './components/SensorSelector';
import SimulatorToolbar from './components/SimulatorToolbar';
import ThresholdsModal from './components/ThresholdsModal';

const MAX_LIVE_SERIES_POINTS = 75;

export default function App() {
  const [wsConnected, setWsConnected] = useState(false);
  const [sensorCatalog, setSensorCatalog] = useState({});
  const [snapshot, setSnapshot] = useState(null);
  const [recentSeries, setRecentSeries] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [thresholds, setThresholds] = useState(null);
  const [simulator, setSimulator] = useState(null);

  // User-selected sensors displayed on dashboard (starts empty per Section 5)
  const [selectedSensorIds, setSelectedSensorIds] = useState([]);

  // Modals / Drawers
  const [inspectedSensorId, setInspectedSensorId] = useState(null);
  const [showHistory, setShowHistory] = useState(false);
  const [showThresholds, setShowThresholds] = useState(false);

  const wsRef = useRef(null);
  const reconnectTimerRef = useRef(null);

  const applyInitBundle = useCallback((bundle) => {
    if (bundle.sensor_catalog) setSensorCatalog(bundle.sensor_catalog);
    if (bundle.snapshot) setSnapshot(bundle.snapshot);
    if (Array.isArray(bundle.recent_series)) setRecentSeries(bundle.recent_series);
    if (Array.isArray(bundle.recent_alerts)) setAlerts(bundle.recent_alerts);
    if (bundle.thresholds) setThresholds(bundle.thresholds);
    if (bundle.simulator !== undefined) setSimulator(bundle.simulator);
  }, []);

  // Connect WebSocket with automatic reconnect; fallback initial fetch via /api/state
  useEffect(() => {
    let cancelled = false;

    fetch('/api/state')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled && data) applyInitBundle(data);
      })
      .catch(() => {});

    const connectWs = () => {
      if (cancelled) return;
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const url = `${proto}//${window.location.host}/ws/live`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!cancelled) setWsConnected(true);
      };

      ws.onmessage = (ev) => {
        if (cancelled) return;
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === 'init') {
            applyInitBundle(msg);
          } else if (msg.type === 'tick' && msg.snapshot) {
            const snap = msg.snapshot;
            setSnapshot(snap);
            if (snap.simulator) setSimulator(snap.simulator);

            const compactPoint = {
              timestamp_ms: snap.timestamp_ms,
              timestamp_iso: snap.timestamp_iso,
              arduino_connected: snap.arduino_connected,
              sensors: Object.fromEntries(
                Object.entries(snap.sensors || {}).map(([sid, s]) => [
                  sid,
                  {
                    display_value: s.display_value,
                    raw_value: s.raw_value,
                    is_valid: s.is_valid,
                    health_status: s.health_status,
                  },
                ])
              ),
            };
            setRecentSeries((prev) => [
              ...prev.slice(-(MAX_LIVE_SERIES_POINTS - 1)),
              compactPoint,
            ]);

            if (Array.isArray(msg.new_alerts) && msg.new_alerts.length > 0) {
              setAlerts((prev) => [...msg.new_alerts.slice().reverse(), ...prev].slice(0, 150));
            }
          }
        } catch {
          // ignore malformed packet
        }
      };

      ws.onclose = () => {
        if (cancelled) return;
        setWsConnected(false);
        reconnectTimerRef.current = setTimeout(connectWs, 2000);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connectWs();

    return () => {
      cancelled = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [applyInitBundle]);

  const handleAddSensors = (idsToAdd) => {
    setSelectedSensorIds((prev) => {
      const next = [...prev];
      idsToAdd.forEach((id) => {
        if (!next.includes(id)) next.push(id);
      });
      return next;
    });
  };

  const handleRemoveSensor = (sid) => {
    setSelectedSensorIds((prev) => prev.filter((id) => id !== sid));
  };

  const handleSelectScenario = async (scenarioId) => {
    try {
      const resp = await fetch('/api/simulator/scenario', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario: scenarioId }),
      });
      if (resp.ok) {
        const data = await resp.json();
        setSimulator(data.simulator);
      }
    } catch {
      // ignore network error
    }
  };

  const arduinoConnected = Boolean(snapshot?.arduino_connected);
  const env = snapshot?.environmental || { status: 'Normal', is_fire_alert: false, reason: '' };
  const storageStats = snapshot?.storage_stats || {};
  const mlSummary = snapshot?.ml_summary || {};
  const ingestionMode = snapshot?.ingestion_mode || 'connecting';

  const envBannerClass = env.is_fire_alert
    ? 'env-banner fire'
    : env.status === 'Elevated Thermal Watch'
    ? 'env-banner watch'
    : 'env-banner normal';

  return (
    <div className="app-container">
      {/* Top System Header */}
      <header className="top-header">
        <div>
          <h1 className="brand-title">
            Predictive Condition Monitoring &amp; Maintenance Analytics
          </h1>
          <p className="brand-subtitle">
            Arduino Uno &rarr; USB Serial &rarr; ROS 2 Humble &rarr; Python ML &amp; SQLite &rarr; WebSocket &rarr; React
          </p>
        </div>

        <div className="status-strip">
          <span className={wsConnected ? 'badge badge-ok' : 'badge badge-crit'}>
            WebSocket: {wsConnected ? 'Live' : 'Reconnecting'}
          </span>
          <span className={arduinoConnected ? 'badge badge-ok' : 'badge badge-crit'}>
            Arduino Uno: {arduinoConnected ? 'Connected' : 'Disconnected'}
          </span>
          <span className="badge badge-neutral">
            Source: {ingestionMode.toUpperCase()}
          </span>
          <span className="badge badge-neutral" title="Continuous SQLite database record count">
            SQLite Records: {storageStats.total_readings ?? 0}
          </span>
          <span className="badge badge-neutral" title="Multivariate IsolationForest maintenance risk">
            ML Risk: {Number(mlSummary.maintenance_risk_pct ?? 0).toFixed(1)}%
          </span>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setShowHistory((v) => !v)}
          >
            {showHistory ? 'Hide DB History' : 'Historical DB'}
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setShowThresholds(true)}
          >
            Thresholds
          </button>
        </div>
      </header>

      {/* Environmental Hazard Status Banner (Kept Strictly Separate from Sensor Health) */}
      <div className={envBannerClass} role="status">
        <div>
          <strong>Environmental Condition: {env.status}</strong>
          {env.reason ? ` — ${env.reason}` : ''}
        </div>
        <div style={{ fontSize: '0.77rem', fontWeight: 600 }}>
          {env.is_fire_alert
            ? 'CRITICAL HAZARD ALERT (Environmental — Not a Sensor Malfunction)'
            : 'Environmental Hazard Monitor Active'}
        </div>
      </div>

      {/* Interactive Fault & Fire Verification Bar (shown when running in simulator mode) */}
      {simulator && (
        <SimulatorToolbar
          simulator={simulator}
          onSelectScenario={handleSelectScenario}
        />
      )}

      {/* Historical SQLite Query & Graph Panel */}
      {showHistory && (
        <HistoricalPanel
          sensorCatalog={sensorCatalog}
          onClose={() => setShowHistory(false)}
        />
      )}

      {/* Sensor Selection Bar */}
      <SensorSelector
        sensorCatalog={sensorCatalog}
        activeSensorIds={selectedSensorIds}
        onAddSensors={handleAddSensors}
        onRemoveSensor={handleRemoveSensor}
        onClearAll={() => setSelectedSensorIds([])}
        onInspectSensor={(sid) => setInspectedSensorId(sid)}
      />

      {/* Dynamically Displayed Selected Sensor Monitoring Cards */}
      {selectedSensorIds.length === 0 ? (
        <div className="empty-dashboard-state">
          <div style={{ fontWeight: 700, fontSize: '0.96rem', marginBottom: '4px', color: '#1e293b' }}>
            No Sensors Currently Displayed on Dashboard
          </div>
          <div style={{ fontSize: '0.84rem' }}>
            Use the <strong>Sensor Selection</strong> panel above to select and add{' '}
            <em>DS18B20 Temperature</em>, <em>Flame Sensor</em>, <em>DHT11 Temperature</em>, or{' '}
            <em>DHT11 Humidity</em> (individually or simultaneously).
            <br />
            Continuous background data collection and SQLite storage remain active ({storageStats.total_readings ?? 0} cycles stored).
          </div>
        </div>
      ) : (
        <main className="sensor-cards-grid" aria-label="Selected Sensor Monitoring Cards">
          {selectedSensorIds.map((sid) => {
            const meta = sensorCatalog[sid] || {};
            const sState = snapshot?.sensors?.[sid] || {};
            const seriesPoints = recentSeries.map((pt) => ({
              timestamp_ms: pt.timestamp_ms,
              display_value: pt.sensors?.[sid]?.display_value ?? 0,
              is_valid: Boolean(pt.sensors?.[sid]?.is_valid),
            }));

            return (
              <SensorCard
                key={sid}
                sensorId={sid}
                meta={meta}
                sensorState={sState}
                seriesPoints={seriesPoints}
                onInspect={(id) => setInspectedSensorId(id)}
                onRemove={handleRemoveSensor}
              />
            );
          })}
        </main>
      )}

      {/* Real-Time & Historical Alerts Panel */}
      <AlertsPanel alerts={alerts} />

      {/* Individual Sensor Info Modal */}
      {inspectedSensorId && (
        <SensorInfoModal
          sensorId={inspectedSensorId}
          meta={sensorCatalog[inspectedSensorId]}
          sensorState={snapshot?.sensors?.[inspectedSensorId]}
          onClose={() => setInspectedSensorId(null)}
        />
      )}

      {/* Configurable Thresholds Modal */}
      {showThresholds && (
        <ThresholdsModal
          thresholds={thresholds}
          onSave={(updated) => setThresholds(updated)}
          onClose={() => setShowThresholds(false)}
        />
      )}
    </div>
  );
}
