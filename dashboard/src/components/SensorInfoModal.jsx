import React from 'react';

export default function SensorInfoModal({
  sensorId,
  meta = {},
  sensorState = {},
  onClose,
}) {
  if (!sensorId) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="panel-header">
          <h2 className="panel-title">{meta.name} &mdash; Individual Sensor Information</h2>
          <button type="button" className="btn btn-sm" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="kv-grid">
          <div className="kv-label">Hardware Module</div>
          <div>{meta.hardware_module}</div>

          <div className="kv-label">Measurement Type</div>
          <div>{meta.measurement_type}</div>

          <div className="kv-label">Arduino Uno Pin</div>
          <div>{meta.arduino_pin}</div>

          <div className="kv-label">ROS 2 Humble Topic</div>
          <div><code>{meta.ros2_topic}</code> ({meta.ros2_msg_type})</div>

          <div className="kv-label">Operating Range</div>
          <div>{meta.operating_range}</div>

          <div className="kv-label">Hardware Resolution</div>
          <div>{meta.resolution}</div>

          <div className="kv-label">Connection Status</div>
          <div><strong>{sensorState.connection_status || 'Disconnected'}</strong></div>

          <div className="kv-label">Displayed Reading</div>
          <div>
            <strong>{sensorState.display_value ?? 0} {meta.unit}</strong>{' '}
            <span style={{ color: '#64748b', fontSize: '0.78rem' }}>
              (Database Raw Value:{' '}
              {sensorState.is_valid && sensorState.raw_value !== null && sensorState.raw_value !== undefined
                ? `${sensorState.raw_value} [Valid=1]`
                : 'NULL [Missing/Invalid=0]'}
              )
            </span>
          </div>

          <div className="kv-label">Sensor Health Status</div>
          <div><strong>{sensorState.health_status || 'Insufficient Data'}</strong></div>

          <div className="kv-label">Unchanged Duration</div>
          <div>{Number(sensorState.unchanged_duration_sec ?? 0).toFixed(1)} s (Method 1: Constant-Value Tracker)</div>

          <div className="kv-label">Last Step Delta</div>
          <div>{Number(sensorState.last_step_delta ?? 0).toFixed(2)} {meta.unit} (Method 3: Variation Tracker)</div>

          <div className="kv-label">ML Anomaly Score</div>
          <div>
            <strong>{Number(sensorState.ml_anomaly_score ?? 0).toFixed(3)}</strong> &mdash;{' '}
            {sensorState.ml_condition_label || 'Normal'} (scikit-learn IsolationForest)
          </div>
        </div>

        <div style={{ marginTop: '12px' }}>
          <div style={{ fontSize: '0.82rem', fontWeight: 700, marginBottom: '5px' }}>
            Active Condition Assessment Reasons:
          </div>
          <ul style={{ margin: 0, paddingLeft: '18px', fontSize: '0.82rem', color: '#334155' }}>
            {(sensorState.health_reasons || ['Awaiting telemetry...']).map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
