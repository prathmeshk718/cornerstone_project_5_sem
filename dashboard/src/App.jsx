import React, { useEffect, useState, useRef } from 'react'

/**
 * Simple, lightweight SVG Line Chart component for real-time sensor monitoring
 */
function SimpleLineChart({ data, series, minY, maxY, height = 165, unit = '' }) {
  const width = 620
  const padLeft = 40
  const padRight = 14
  const padTop = 14
  const padBottom = 24
  const plotW = width - padLeft - padRight
  const plotH = height - padTop - padBottom

  if (!data || data.length === 0) {
    return (
      <svg viewBox={`0 0 ${width} ${height}`} className="svg-chart">
        <text x={width / 2} y={height / 2} textAnchor="middle" fill="#64748b" fontSize="12">
          Waiting for real-time sensor stream...
        </text>
      </svg>
    )
  }

  // Compute dynamic bounds if needed
  let calcMin = minY
  let calcMax = maxY
  series.forEach((s) => {
    data.forEach((pt) => {
      const v = Number(pt[s.key] ?? 0)
      if (v < calcMin) calcMin = Math.floor(v - 2)
      if (v > calcMax) calcMax = Math.ceil(v + 2)
    })
  })
  const rangeY = Math.max(1, calcMax - calcMin)

  const getX = (idx) => {
    if (data.length <= 1) return padLeft + plotW / 2
    return padLeft + (idx / (data.length - 1)) * plotW
  }

  const getY = (val) => {
    const clamped = Math.max(calcMin, Math.min(calcMax, Number(val ?? 0)))
    return padTop + plotH - ((clamped - calcMin) / rangeY) * plotH
  }

  const gridTicks = [0, 0.5, 1].map((frac) => {
    const val = calcMin + frac * rangeY
    const y = padTop + plotH - frac * plotH
    return { val: Math.round(val), y }
  })

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="svg-chart">
      {/* Horizontal grid lines */}
      {gridTicks.map((t, i) => (
        <g key={i}>
          <line
            x1={padLeft}
            y1={t.y}
            x2={width - padRight}
            y2={t.y}
            stroke="#e2e8f0"
            strokeDasharray="3 3"
          />
          <text x={padLeft - 6} y={t.y + 4} textAnchor="end" fontSize="10" fill="#64748b">
            {t.val}{unit}
          </text>
        </g>
      ))}

      {/* Series polylines */}
      {series.map((s) => {
        const points = data.map((pt, idx) => `${getX(idx).toFixed(1)},${getY(pt[s.key]).toFixed(1)}`).join(' ')
        const lastPt = data[data.length - 1]
        return (
          <g key={s.key}>
            <polyline
              fill="none"
              stroke={s.color}
              strokeWidth="2.2"
              points={points}
            />
            {lastPt && (
              <circle
                cx={getX(data.length - 1)}
                cy={getY(lastPt[s.key])}
                r="3.2"
                fill={s.color}
              />
            )}
          </g>
        )
      })}

      {/* X-axis timestamps */}
      {data.length > 0 && (
        <>
          <text x={padLeft} y={height - 6} fontSize="10" fill="#64748b">
            {data[0].time_label || ''}
          </text>
          <text x={width - padRight} y={height - 6} textAnchor="end" fontSize="10" fill="#64748b">
            {data[data.length - 1].time_label || 'Latest'}
          </text>
        </>
      )}
    </svg>
  )
}

export default function App() {
  const [wsConnected, setWsConnected] = useState(false)
  const [systemState, setSystemState] = useState(null)
  const [latestPacket, setLatestPacket] = useState(null)
  const [streamHistory, setStreamHistory] = useState([])
  const [retraining, setRetraining] = useState(false)
  const [pinSaveMsg, setPinSaveMsg] = useState('')

  // User-selectable sensor visibility (Expected Feature #6)
  const [visibleSensors, setVisibleSensors] = useState({
    ds18b20: true,
    dht11: true,
    yg1006: true,
    smoke: true,
  })

  // Editable custom Arduino Pin & Hardware Config state
  const [pinForm, setPinForm] = useState({
    ds18b20_pin: 'D2',
    dht11_pin: 'D3',
    yg1006_digital_pin: 'D4',
    yg1006_analog_pin: 'A1',
    smoke_analog_pin: 'A0',
    smoke_sensor_model: 'Configurable MQ-Series (e.g., MQ-2 / MQ-135)',
    serial_port: '/dev/ttyACM0',
    baud_rate: 115200,
  })

  const wsRef = useRef(null)

  const fetchFullStatus = async () => {
    try {
      const res = await fetch('/api/status')
      if (!res.ok) return
      const data = await res.json()
      setSystemState(data)
      if (data.latest_packet && Object.keys(data.latest_packet).length > 0) {
        setLatestPacket(data.latest_packet)
      }
      if (Array.isArray(data.recent_stream)) {
        setStreamHistory(data.recent_stream)
      }
      if (data.hardware_config?.hardware) {
        const hw = data.hardware_config.hardware
        setPinForm({
          ds18b20_pin: hw.pins?.ds18b20_pin || 'D2',
          dht11_pin: hw.pins?.dht11_pin || 'D3',
          yg1006_digital_pin: hw.pins?.yg1006_digital_pin || 'D4',
          yg1006_analog_pin: hw.pins?.yg1006_analog_pin || 'A1',
          smoke_analog_pin: hw.pins?.smoke_analog_pin || 'A0',
          smoke_sensor_model: hw.sensors?.smoke?.model || 'MQ-2 / MQ-135',
          serial_port: hw.serial_port || '/dev/ttyACM0',
          baud_rate: hw.baud_rate || 115200,
        })
      }
    } catch (err) {
      console.error('Status fetch error:', err)
    }
  }

  useEffect(() => {
    fetchFullStatus()

    let reconnectTimer = null
    const connectWebSocket = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsUrl = `${protocol}//${window.location.host}/ws/telemetry`
      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onopen = () => {
        setWsConnected(true)
      }

      ws.onmessage = (event) => {
        try {
          const pkt = JSON.parse(event.data)
          if (pkt.type === 'telemetry_update') {
            setLatestPacket(pkt)
            const timeLabel = new Date(pkt.timestamp_unix * 1000).toLocaleTimeString()
            setStreamHistory((prev) => {
              const next = [
                ...prev,
                {
                  seq: pkt.seq,
                  time_label: timeLabel,
                  ds18b20_temp_c: pkt.sensors.ds18b20_temp_c,
                  dht11_temp_c: pkt.sensors.dht11_temp_c,
                  dht11_humidity_pct: pkt.sensors.dht11_humidity_pct,
                  yg1006_flame: pkt.sensors.yg1006_flame,
                  smoke_raw_adc: pkt.sensors.smoke_raw_adc,
                  temp_differential_c: pkt.sensors.temp_differential_c,
                },
              ]
              return next.slice(-50)
            })
          }
        } catch (e) {
          console.error('WS parse error:', e)
        }
      }

      ws.onclose = () => {
        setWsConnected(false)
        reconnectTimer = setTimeout(connectWebSocket, 2000)
      }

      ws.onerror = () => {
        ws.close()
      }
    }

    connectWebSocket()
    return () => {
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (wsRef.current) wsRef.current.close()
    }
  }, [])

  const handleToggleSensor = (key) => {
    setVisibleSensors((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  const handleScenarioChange = async (e) => {
    const newScenario = e.target.value
    await fetch('/api/source/configure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario: newScenario, sync_operator_label: true }),
    })
    fetchFullStatus()
  }

  const handleModeChange = async (e) => {
    const newMode = e.target.value
    await fetch('/api/source/configure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: newMode }),
    })
    fetchFullStatus()
  }

  const handleToggleRecording = async () => {
    const currentRec = latestPacket?.logger?.is_recording ?? true
    await fetch('/api/logger/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'set_recording', is_recording: !currentRec }),
    })
    fetchFullStatus()
  }

  const handleOperatorLabelChange = async (e) => {
    const labelId = Number(e.target.value)
    await fetch('/api/logger/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'set_label', operator_label: labelId }),
    })
    fetchFullStatus()
  }

  const handleNewSession = async () => {
    await fetch('/api/logger/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'new_session', reset_main_csv: false }),
    })
    fetchFullStatus()
  }

  const handleRetrainModels = async () => {
    setRetraining(true)
    try {
      const res = await fetch('/api/ml/retrain', { method: 'POST' })
      const data = await res.json()
      if (data.ml_evaluation) {
        setSystemState((prev) => ({
          ...prev,
          ml_evaluation: data.ml_evaluation,
          historical_summary: data.historical_summary,
        }))
      }
    } finally {
      setRetraining(false)
    }
  }

  const handleSaveHardwareConfig = async (e) => {
    e.preventDefault()
    setPinSaveMsg('Saving...')
    const res = await fetch('/api/config/hardware', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        pins: {
          ds18b20_pin: pinForm.ds18b20_pin,
          dht11_pin: pinForm.dht11_pin,
          yg1006_digital_pin: pinForm.yg1006_digital_pin,
          yg1006_analog_pin: pinForm.yg1006_analog_pin,
          smoke_analog_pin: pinForm.smoke_analog_pin,
        },
        smoke_sensor_model: pinForm.smoke_sensor_model,
        serial_port: pinForm.serial_port,
        baud_rate: Number(pinForm.baud_rate),
      }),
    })
    const data = await res.json()
    if (data.status === 'ok') {
      setPinSaveMsg('Saved to hardware_config.json & sensor_acquisition.ino')
      setTimeout(() => setPinSaveMsg(''), 4000)
      fetchFullStatus()
    }
  }

  const sensors = latestPacket?.sensors || {
    ds18b20_temp_c: 42.5,
    dht11_temp_c: 26.4,
    dht11_humidity_pct: 48.0,
    yg1006_flame: 0,
    yg1006_ir_raw: 955,
    smoke_raw_adc: 160,
    temp_differential_c: 16.1,
  }

  const analytics = latestPacket?.analytics || {
    operating_condition: 'Normal Operating Condition',
    risk_level: 'LOW',
    health_index: 96,
    is_anomaly: false,
    anomaly_score: 0.08,
    predicted_label: 'Normal Operating Condition',
    prediction_confidence: 0.98,
    sensor_statuses: {
      ds18b20: 'NORMAL',
      dht11: 'NORMAL',
      yg1006: 'NORMAL',
      smoke: 'NORMAL',
    },
    predictive_trends: {
      ds18b20_slope_c_per_min: 0.0,
      smoke_slope_adc_per_min: 0.0,
      trend_status: 'STABLE',
    },
    fault_alerts: ['No active equipment or environmental faults detected.'],
    maintenance_warnings: ['All sensor readings are within normal operating baselines.'],
    suggested_actions: ['Continue routine condition monitoring and scheduled preventive logging.'],
  }

  const loggerInfo = latestPacket?.logger || systemState?.logger || {
    is_recording: true,
    current_operator_label: 0,
    session_row_count: 0,
    total_row_count: 300,
    available_labels: [],
  }

  const connInfo = latestPacket?.connection || {
    source_mode: 'simulator',
    scenario: 'normal',
    serial_port: pinForm.serial_port,
    baud_rate: pinForm.baud_rate,
  }

  const mlEval = systemState?.ml_evaluation
  const alertBannerClass =
    analytics.risk_level === 'CRITICAL' || analytics.risk_level === 'HIGH'
      ? 'critical'
      : analytics.risk_level === 'MODERATE'
      ? 'warning'
      : 'normal'

  return (
    <div className="app-container">
      {/* 1. Top Header & Connection Status (Expected Feature #9) */}
      <header className="top-header">
        <div className="header-title">
          <h1>Predictive Condition Monitoring &amp; Maintenance Analytics</h1>
          <p>
            Arduino Uno &rarr; USB Serial &rarr; ROS 2 Humble (Ubuntu 22.04) &rarr; Python ML Framework &rarr; WebSocket &rarr; React Dashboard
          </p>
        </div>

        <div className="connection-pills">
          <span className={`pill ${wsConnected ? 'ok' : 'crit'}`}>
            <span className="dot" />
            WebSocket: {wsConnected ? 'Connected (Live 1 Hz)' : 'Reconnecting...'}
          </span>

          <span className="pill ok">
            <span className="dot" />
            ROS 2 Stream: {connInfo.source_mode === 'serial' ? `Serial (${connInfo.serial_port})` : `ROS 2 Simulator (${connInfo.scenario})`}
          </span>

          <span className={`pill ${loggerInfo.is_recording ? 'ok' : 'warn'}`}>
            <span className="dot" />
            CSV Logger: {loggerInfo.is_recording ? `Recording (${loggerInfo.total_row_count} rows)` : 'Paused'}
          </span>
        </div>
      </header>

      {/* 2. Simple Visual Alert Banner for Operating Condition (Expected Feature #8) */}
      <div className={`alert-banner ${alertBannerClass}`}>
        <div>
          <strong>
            System Status: {analytics.operating_condition} &mdash; Risk Level: {analytics.risk_level} (Equipment Health Index: {analytics.health_index}%)
          </strong>
          <span>
            ML Diagnosis: <b>{analytics.predicted_label}</b> ({Math.round((analytics.prediction_confidence || 0.95) * 100)}% confidence)
            {' | '}
            {analytics.fault_alerts?.[0]}
          </span>
        </div>
        <div style={{ fontSize: '0.78rem', whiteSpace: 'nowrap', fontWeight: 600 }}>
          Packet #{latestPacket?.seq || 0}
        </div>
      </div>

      {/* 3. Sensor Visibility Selector & Data Source / Scenario Bar (Expected Feature #6) */}
      <div className="control-bar">
        <div className="sensor-toggles">
          <span className="label-title">Display Sensors:</span>
          <label className="toggle-item">
            <input
              type="checkbox"
              checked={visibleSensors.ds18b20}
              onChange={() => handleToggleSensor('ds18b20')}
            />
            DS18B20 (Equipment Temp)
          </label>
          <label className="toggle-item">
            <input
              type="checkbox"
              checked={visibleSensors.dht11}
              onChange={() => handleToggleSensor('dht11')}
            />
            DHT11 (Ambient Temp &amp; Humidity)
          </label>
          <label className="toggle-item">
            <input
              type="checkbox"
              checked={visibleSensors.yg1006}
              onChange={() => handleToggleSensor('yg1006')}
            />
            YG1006 (Flame / IR)
          </label>
          <label className="toggle-item">
            <input
              type="checkbox"
              checked={visibleSensors.smoke}
              onChange={() => handleToggleSensor('smoke')}
            />
            Smoke Sensor (Analog ADC)
          </label>
        </div>

        <div className="source-controls">
          <label style={{ fontWeight: 600, color: '#475569' }}>Input Mode:</label>
          <select value={connInfo.source_mode || 'simulator'} onChange={handleModeChange}>
            <option value="simulator">Hardware Simulator (Test Regimes)</option>
            <option value="serial">Physical Arduino USB Serial ({pinForm.serial_port})</option>
          </select>

          <label style={{ fontWeight: 600, color: '#475569' }}>Test Condition:</label>
          <select value={connInfo.scenario || 'normal'} onChange={handleScenarioChange}>
            {(systemState?.source?.available_scenarios || [
              { id: 'normal', name: 'Normal Operating Condition' },
              { id: 'equipment_overheat', name: 'Equipment Overheating (DS18B20 Fault)' },
              { id: 'ambient_stress', name: 'Environmental Temp & Humidity Stress (DHT11)' },
              { id: 'smoke_anomaly', name: 'Smoke / Gas Emission Anomaly' },
              { id: 'flame_hazard', name: 'Critical Infrared Flame Detection (YG1006)' },
              { id: 'gradual_degradation', name: 'Predictive Maintenance Drift (Gradual Heating)' },
            ]).map((sc) => (
              <option key={sc.id} value={sc.id}>
                {sc.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* 4. Individual Sensor Cards (Expected Features #1, #2, #3, #4, #5) */}
      <div className="sensor-grid">
        {visibleSensors.ds18b20 && (
          <div className={`sensor-card status-${analytics.sensor_statuses?.ds18b20 || 'NORMAL'}`}>
            <div className="card-header">
              <div>
                <h3 className="card-title">DS18B20 Temperature Sensor</h3>
                <p className="card-subtitle">Equipment Surface / Core Temperature (Independent)</p>
              </div>
              <span className={`status-badge ${analytics.sensor_statuses?.ds18b20 || 'NORMAL'}`}>
                {analytics.sensor_statuses?.ds18b20 || 'NORMAL'}
              </span>
            </div>

            <div className="reading-row">
              <div>
                <span className="reading-primary">{Number(sensors.ds18b20_temp_c).toFixed(2)}</span>
                <span className="reading-unit">&deg;C</span>
              </div>
              <div style={{ fontSize: '0.8rem', color: '#475569' }}>
                &Delta;T vs Ambient: <b>+{Number(sensors.temp_differential_c).toFixed(1)} &deg;C</b>
              </div>
            </div>

            <div className="card-meta">
              <span>Normal Range: 20&ndash;52 &deg;C</span>
              <span className="pin-tag">Arduino Pin: {pinForm.ds18b20_pin}</span>
            </div>
          </div>
        )}

        {visibleSensors.dht11 && (
          <div className={`sensor-card status-${analytics.sensor_statuses?.dht11 || 'NORMAL'}`}>
            <div className="card-header">
              <div>
                <h3 className="card-title">DHT11 Temp &amp; Humidity Sensor</h3>
                <p className="card-subtitle">Surrounding Environmental Conditions (Independent)</p>
              </div>
              <span className={`status-badge ${analytics.sensor_statuses?.dht11 || 'NORMAL'}`}>
                {analytics.sensor_statuses?.dht11 || 'NORMAL'}
              </span>
            </div>

            <div className="reading-row">
              <div>
                <span className="reading-primary">{Number(sensors.dht11_temp_c).toFixed(1)}</span>
                <span className="reading-unit">&deg;C</span>
              </div>
              <div>
                <span className="reading-primary">{Number(sensors.dht11_humidity_pct).toFixed(1)}</span>
                <span className="reading-unit">% RH</span>
              </div>
            </div>

            <div className="card-meta">
              <span>Normal: 15&ndash;34 &deg;C | 30&ndash;68% RH</span>
              <span className="pin-tag">Arduino Pin: {pinForm.dht11_pin}</span>
            </div>
          </div>
        )}

        {visibleSensors.yg1006 && (
          <div className={`sensor-card status-${analytics.sensor_statuses?.yg1006 || 'NORMAL'}`}>
            <div className="card-header">
              <div>
                <h3 className="card-title">YG1006 Phototransistor</h3>
                <p className="card-subtitle">Infrared Radiation / Flame Detection Sensor</p>
              </div>
              <span className={`status-badge ${analytics.sensor_statuses?.yg1006 || 'NORMAL'}`}>
                {analytics.sensor_statuses?.yg1006 || 'NORMAL'}
              </span>
            </div>

            <div className="reading-row">
              <div>
                <span
                  className="reading-primary"
                  style={{
                    fontSize: '1.35rem',
                    color: sensors.yg1006_flame === 1 ? '#dc2626' : '#166534',
                  }}
                >
                  {sensors.yg1006_flame === 1 ? 'FLAME DETECTED' : 'NO FLAME (SAFE)'}
                </span>
              </div>
              <div style={{ fontSize: '0.8rem', color: '#475569' }}>
                IR Raw: <b>{sensors.yg1006_ir_raw}</b>
              </div>
            </div>

            <div className="card-meta">
              <span>Digital Status: {sensors.yg1006_flame}</span>
              <span className="pin-tag">
                Pin: {pinForm.yg1006_digital_pin} / {pinForm.yg1006_analog_pin}
              </span>
            </div>
          </div>
        )}

        {visibleSensors.smoke && (
          <div className={`sensor-card status-${analytics.sensor_statuses?.smoke || 'NORMAL'}`}>
            <div className="card-header">
              <div>
                <h3 className="card-title">Analog Smoke Sensor</h3>
                <p className="card-subtitle">{pinForm.smoke_sensor_model}</p>
              </div>
              <span className={`status-badge ${analytics.sensor_statuses?.smoke || 'NORMAL'}`}>
                {analytics.sensor_statuses?.smoke || 'NORMAL'}
              </span>
            </div>

            <div className="reading-row">
              <div>
                <span className="reading-primary">{sensors.smoke_raw_adc}</span>
                <span className="reading-unit">/ 1023 ADC</span>
              </div>
              <div style={{ fontSize: '0.8rem', color: '#475569' }}>
                Level: <b>{((sensors.smoke_raw_adc / 1023) * 100).toFixed(1)}%</b>
              </div>
            </div>

            <div className="card-meta">
              <span>Clean Air Baseline: ~160 ADC (Warn &ge; 300)</span>
              <span className="pin-tag">Arduino Pin: {pinForm.smoke_analog_pin}</span>
            </div>
          </div>
        )}
      </div>

      {/* 5. Real-Time Sensor Graphs & Maintenance Analytics (Expected Feature #7 + Section 7) */}
      <div className="two-col-layout">
        {/* Left: Basic Real-Time Graphs */}
        <div className="section-card">
          <h2>
            <span>Real-Time Sensor Variations Over Time</span>
            <span style={{ fontSize: '0.76rem', fontWeight: 500, color: '#64748b' }}>
              Last {streamHistory.length} samples (1 Hz)
            </span>
          </h2>

          {(visibleSensors.ds18b20 || visibleSensors.dht11) && (
            <div className="chart-box">
              <div className="chart-header">
                <strong>Temperature (&deg;C) &amp; Relative Humidity (%)</strong>
                <div className="chart-legend">
                  {visibleSensors.ds18b20 && (
                    <span className="legend-item">
                      <span className="legend-swatch" style={{ background: '#dc2626' }} />
                      DS18B20 Equip Temp (&deg;C)
                    </span>
                  )}
                  {visibleSensors.dht11 && (
                    <>
                      <span className="legend-item">
                        <span className="legend-swatch" style={{ background: '#2563eb' }} />
                        DHT11 Ambient Temp (&deg;C)
                      </span>
                      <span className="legend-item">
                        <span className="legend-swatch" style={{ background: '#0d9488' }} />
                        DHT11 Humidity (%)
                      </span>
                    </>
                  )}
                </div>
              </div>
              <SimpleLineChart
                data={streamHistory}
                minY={15}
                maxY={85}
                series={[
                  ...(visibleSensors.ds18b20
                    ? [{ key: 'ds18b20_temp_c', color: '#dc2626' }]
                    : []),
                  ...(visibleSensors.dht11
                    ? [
                        { key: 'dht11_temp_c', color: '#2563eb' },
                        { key: 'dht11_humidity_pct', color: '#0d9488' },
                      ]
                    : []),
                ]}
              />
            </div>
          )}

          {(visibleSensors.smoke || visibleSensors.yg1006) && (
            <div className="chart-box">
              <div className="chart-header">
                <strong>Smoke Sensor Raw Analog Output (0&ndash;1023 ADC)</strong>
                <div className="chart-legend">
                  <span className="legend-item">
                    <span className="legend-swatch" style={{ background: '#d97706' }} />
                    Smoke Raw ADC
                  </span>
                </div>
              </div>
              <SimpleLineChart
                data={streamHistory}
                minY={0}
                maxY={750}
                series={[{ key: 'smoke_raw_adc', color: '#d97706' }]}
              />
            </div>
          )}
        </div>

        {/* Right: Predictive Maintenance Analytics */}
        <div className="section-card">
          <h2>
            <span>ML Condition Monitoring &amp; Maintenance Analytics</span>
            <span className={`status-badge ${analytics.risk_level === 'LOW' ? 'NORMAL' : analytics.risk_level === 'MODERATE' ? 'WARNING' : 'CRITICAL'}`}>
              Risk: {analytics.risk_level}
            </span>
          </h2>

          <div className="kv-grid">
            <div className="kv-box">
              <div className="kv-label">Operating Condition</div>
              <div className="kv-val">{analytics.operating_condition}</div>
            </div>
            <div className="kv-box">
              <div className="kv-label">ML Fault Classification (RF)</div>
              <div className="kv-val">{analytics.predicted_label}</div>
            </div>
            <div className="kv-box">
              <div className="kv-label">Isolation Forest Anomaly Check</div>
              <div className="kv-val">
                {analytics.is_anomaly ? 'ANOMALY DETECTED' : 'Normal Pattern'} (Score: {analytics.anomaly_score})
              </div>
            </div>
            <div className="kv-box">
              <div className="kv-label">Predictive Thermal / Smoke Trend</div>
              <div className="kv-val">
                {analytics.predictive_trends?.ds18b20_slope_c_per_min >= 0 ? '+' : ''}
                {analytics.predictive_trends?.ds18b20_slope_c_per_min || 0} &deg;C/min |{' '}
                {analytics.predictive_trends?.trend_status || 'STABLE'}
              </div>
            </div>
          </div>

          <div className="info-list-block">
            <h3>Active Fault Alerts</h3>
            <ul>
              {(analytics.fault_alerts || []).map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          </div>

          <div className="info-list-block">
            <h3>Predictive Maintenance Warnings</h3>
            <ul>
              {(analytics.maintenance_warnings || []).map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          </div>

          <div className="info-list-block">
            <h3>Suggested Inspection &amp; Maintenance Actions</h3>
            <ul>
              {(analytics.suggested_actions || []).map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          </div>
        </div>
      </div>

      {/* 6. Bottom Grid: Data Collection Pipeline & ML Model Evaluation */}
      <div className="bottom-grid">
        {/* Left: Data Acquisition & CSV Logging Pipeline + Hardware Pin Config */}
        <div className="section-card">
          <h2>
            <span>1. Sensor Data Logging Pipeline &amp; Hardware Pin Config</span>
            <a
              href="/api/logger/download?scope=all"
              className="btn primary"
              style={{ textDecoration: 'none' }}
            >
              Download CSV Dataset ({loggerInfo.total_row_count} rows)
            </a>
          </h2>

          <p style={{ fontSize: '0.8rem', color: '#475569', margin: '0 0 10px' }}>
            Continuously records independent DS18B20, DHT11, YG1006, and Smoke ADC readings to{' '}
            <code>data/sensor_readings_history.csv</code> and SQLite (<code>data/condition_monitoring.db</code>).
          </p>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'center', marginBottom: '14px' }}>
            <button
              type="button"
              className={`btn ${loggerInfo.is_recording ? 'danger' : 'primary'}`}
              onClick={handleToggleRecording}
            >
              {loggerInfo.is_recording ? 'Pause CSV Logging' : 'Resume CSV Logging'}
            </button>

            <button type="button" className="btn" onClick={handleNewSession}>
              New Session CSV ({loggerInfo.session_row_count} rows in current session)
            </button>

            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <label style={{ fontSize: '0.78rem', fontWeight: 600 }}>Dataset Label:</label>
              <select
                value={loggerInfo.current_operator_label ?? 0}
                onChange={handleOperatorLabelChange}
              >
                {(loggerInfo.available_labels || [
                  { id: 0, name: '0: Normal Operating Condition' },
                  { id: 1, name: '1: Equipment Overheating (DS18B20)' },
                  { id: 2, name: '2: Environmental Temp/Humidity Anomaly (DHT11)' },
                  { id: 3, name: '3: Smoke / Gas Emission Anomaly' },
                  { id: 4, name: '4: Fire / Flame Hazard (YG1006)' },
                ]).map((lbl) => (
                  <option key={lbl.id} value={lbl.id}>
                    [{lbl.id}] {lbl.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <hr style={{ border: 'none', borderTop: '1px solid #e2e8f0', margin: '12px 0' }} />

          <form onSubmit={handleSaveHardwareConfig}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <strong style={{ fontSize: '0.86rem' }}>
                Custom Arduino Uno Pinout &amp; Smoke Sensor Configuration
              </strong>
              <button type="submit" className="btn primary">
                Update Pins &amp; Sync .ino Sketch
              </button>
            </div>
            {pinSaveMsg && (
              <div style={{ fontSize: '0.78rem', color: '#166534', marginTop: '4px', fontWeight: 600 }}>
                {pinSaveMsg}
              </div>
            )}

            <div className="form-grid">
              <div className="form-field">
                <label>DS18B20 Digital Pin</label>
                <input
                  type="text"
                  value={pinForm.ds18b20_pin}
                  onChange={(e) => setPinForm({ ...pinForm, ds18b20_pin: e.target.value })}
                />
              </div>
              <div className="form-field">
                <label>DHT11 Digital Pin</label>
                <input
                  type="text"
                  value={pinForm.dht11_pin}
                  onChange={(e) => setPinForm({ ...pinForm, dht11_pin: e.target.value })}
                />
              </div>
              <div className="form-field">
                <label>YG1006 Digital Pin (DO)</label>
                <input
                  type="text"
                  value={pinForm.yg1006_digital_pin}
                  onChange={(e) => setPinForm({ ...pinForm, yg1006_digital_pin: e.target.value })}
                />
              </div>
              <div className="form-field">
                <label>YG1006 Analog Pin (AO)</label>
                <input
                  type="text"
                  value={pinForm.yg1006_analog_pin}
                  onChange={(e) => setPinForm({ ...pinForm, yg1006_analog_pin: e.target.value })}
                />
              </div>
              <div className="form-field">
                <label>Smoke Sensor Analog Pin</label>
                <input
                  type="text"
                  value={pinForm.smoke_analog_pin}
                  onChange={(e) => setPinForm({ ...pinForm, smoke_analog_pin: e.target.value })}
                />
              </div>
              <div className="form-field">
                <label>Smoke Sensor Model</label>
                <input
                  type="text"
                  value={pinForm.smoke_sensor_model}
                  onChange={(e) => setPinForm({ ...pinForm, smoke_sensor_model: e.target.value })}
                />
              </div>
              <div className="form-field">
                <label>Ubuntu Serial Port</label>
                <input
                  type="text"
                  value={pinForm.serial_port}
                  onChange={(e) => setPinForm({ ...pinForm, serial_port: e.target.value })}
                />
              </div>
              <div className="form-field">
                <label>Serial Baud Rate</label>
                <input
                  type="number"
                  value={pinForm.baud_rate}
                  onChange={(e) => setPinForm({ ...pinForm, baud_rate: e.target.value })}
                />
              </div>
            </div>
          </form>
        </div>

        {/* Right: Machine Learning Model Evaluation Metrics (Section 6.F) */}
        <div className="section-card">
          <h2>
            <span>2. Machine Learning Model Evaluation (Section 6.F)</span>
            <button
              type="button"
              className="btn primary"
              onClick={handleRetrainModels}
              disabled={retraining}
            >
              {retraining ? 'Training...' : 'Retrain Models on Logged CSV'}
            </button>
          </h2>

          {mlEval ? (
            <>
              <div className="metrics-row">
                <div className="metric-tile">
                  <div className="m-name">Accuracy</div>
                  <div className="m-num">{(mlEval.metrics.accuracy * 100).toFixed(1)}%</div>
                </div>
                <div className="metric-tile">
                  <div className="m-name">Precision</div>
                  <div className="m-num">{(mlEval.metrics.precision_macro * 100).toFixed(1)}%</div>
                </div>
                <div className="metric-tile">
                  <div className="m-name">Recall</div>
                  <div className="m-num">{(mlEval.metrics.recall_macro * 100).toFixed(1)}%</div>
                </div>
                <div className="metric-tile">
                  <div className="m-name">F1-Score</div>
                  <div className="m-num">{(mlEval.metrics.f1_score_macro * 100).toFixed(1)}%</div>
                </div>
              </div>

              <div style={{ fontSize: '0.78rem', color: '#475569', marginBottom: '6px' }}>
                <b>Trained Dataset:</b> {mlEval.dataset_total_samples} samples (Train: {mlEval.train_samples}, Test: {mlEval.test_samples})
                {' | '}
                <b>Top Sensor Features:</b>{' '}
                {(mlEval.feature_importances || [])
                  .slice(0, 4)
                  .map((f) => `${f.feature} (${(f.importance * 100).toFixed(0)}%)`)
                  .join(', ')}
              </div>

              <strong style={{ fontSize: '0.8rem', display: 'block', marginTop: '8px' }}>
                Confusion Matrix (Rows = Actual Condition, Columns = Predicted)
              </strong>
              <table className="simple-table">
                <thead>
                  <tr>
                    <th>Actual \ Pred</th>
                    <th>[0] Normal</th>
                    <th>[1] Equip Heat</th>
                    <th>[2] DHT11 Env</th>
                    <th>[3] Smoke</th>
                    <th>[4] Flame</th>
                  </tr>
                </thead>
                <tbody>
                  {(mlEval.confusion_matrix?.matrix || []).map((row, rIdx) => (
                    <tr key={rIdx}>
                      <td style={{ fontWeight: 600 }}>
                        [{rIdx}] {mlEval.confusion_matrix.labels[rIdx]?.split(' ')[0]}
                      </td>
                      {row.map((cell, cIdx) => (
                        <td
                          key={cIdx}
                          style={{
                            fontWeight: rIdx === cIdx && cell > 0 ? 700 : 400,
                            background:
                              rIdx === cIdx && cell > 0
                                ? '#dcfce7'
                                : cell > 0
                                ? '#fee2e2'
                                : 'transparent',
                          }}
                        >
                          {cell}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <p style={{ fontSize: '0.84rem', color: '#64748b' }}>
              Loading ML evaluation report...
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
