import React from 'react';
import SimpleLineChart from './SimpleLineChart';

function healthBadgeClass(status) {
  switch (status) {
    case 'Healthy':
      return 'badge badge-ok';
    case 'Warning':
      return 'badge badge-warn';
    case 'Maintenance Required':
      return 'badge badge-crit';
    case 'Disconnected':
      return 'badge badge-off';
    case 'Insufficient Data':
    default:
      return 'badge badge-neutral';
  }
}

function connBadgeClass(status) {
  if (status === 'Connected') return 'badge badge-ok';
  return 'badge badge-crit';
}

function cardHealthClass(status) {
  if (status === 'Warning') return 'health-warning';
  if (status === 'Maintenance Required') return 'health-maint';
  if (status === 'Disconnected') return 'health-disconnected';
  return '';
}

export default function SensorCard({
  sensorId,
  meta = {},
  sensorState = {},
  seriesPoints = [],
  onInspect,
  onRemove,
}) {
  const isConnected = sensorState.connection_status === 'Connected';
  const isDigital = Boolean(meta.is_digital_state);

  // Enforce Section 6: Disconnected / No Data ALWAYS displays 0 (never last stale reading)
  let formattedReading = '0';
  if (isConnected && sensorState.is_valid) {
    if (isDigital) {
      const stateNum = Number(sensorState.display_value ?? 0);
      formattedReading = `${stateNum}`;
    } else {
      const num = Number(sensorState.display_value ?? 0);
      formattedReading = Number.isInteger(num) ? num.toFixed(1) : num.toFixed(2);
    }
  } else {
    formattedReading = '0';
  }

  const healthStatus = sensorState.health_status || 'Insufficient Data';
  const connStatus = sensorState.connection_status || 'Disconnected';
  const primaryReason =
    Array.isArray(sensorState.health_reasons) && sensorState.health_reasons.length > 0
      ? sensorState.health_reasons[0]
      : '';

  return (
    <article className={`sensor-card ${cardHealthClass(healthStatus)}`}>
      <div className="card-top">
        <div>
          <h3 className="sensor-title">{meta.name || sensorId}</h3>
          <div className="sensor-pin-hint">
            {meta.arduino_pin} &middot; ROS 2: <code>{meta.ros2_topic}</code>
          </div>
        </div>
        <div style={{ display: 'flex', gap: '6px' }}>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => onInspect(sensorId)}
          >
            Sensor Info
          </button>
          <button
            type="button"
            className="btn btn-sm btn-danger-outline"
            onClick={() => onRemove(sensorId)}
          >
            Remove
          </button>
        </div>
      </div>

      <div className="reading-row">
        <div>
          <span className="reading-value">{formattedReading}</span>
          <span className="reading-unit">{meta.unit}</span>
          {isDigital && isConnected && sensorState.is_valid && (
            <span style={{ marginLeft: '8px', fontSize: '0.8rem', color: '#475569' }}>
              ({Number(sensorState.display_value) === 1 ? 'Flame Detected' : 'No Flame'} &middot; IR{' '}
              {Number(sensorState.extra?.intensity_pct ?? 0).toFixed(1)}%)
            </span>
          )}
        </div>

        <div className="status-kv-row">
          <span className={connBadgeClass(connStatus)}>
            Connection: {connStatus}
          </span>
          <span className={healthBadgeClass(healthStatus)}>
            Health: {healthStatus}
          </span>
        </div>
      </div>

      {primaryReason && (
        <div className="health-reason-box">
          {primaryReason}
        </div>
      )}

      <SimpleLineChart
        points={seriesPoints}
        unit={meta.unit}
        isDigital={isDigital}
      />
    </article>
  );
}
