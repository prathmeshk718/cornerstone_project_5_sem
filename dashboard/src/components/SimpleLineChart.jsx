import React from 'react';

function formatClock(ms) {
  if (!ms) return '';
  const d = new Date(ms);
  return d.toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export default function SimpleLineChart({
  points = [],
  unit = '',
  isDigital = false,
  height = 135,
}) {
  const width = 520;
  const padL = 44;
  const padR = 12;
  const padT = 12;
  const padB = 22;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;

  if (!points.length) {
    return (
      <div className="chart-box" style={{ fontSize: '0.78rem', color: '#64748b', padding: '18px', textAlign: 'center' }}>
        Waiting for time-series samples...
      </div>
    );
  }

  let yMin = 0;
  let yMax = 1;
  if (!isDigital) {
    const vals = points.map((p) => Number(p.display_value ?? 0));
    const rawMin = Math.min(...vals, 0);
    const rawMax = Math.max(...vals, 1);
    const span = Math.max(2, rawMax - rawMin);
    yMin = rawMin >= 0 ? Math.max(0, Math.floor(rawMin - span * 0.15)) : Math.floor(rawMin - span * 0.15);
    yMax = Math.ceil(rawMax + span * 0.15);
    if (yMax <= yMin) yMax = yMin + 5;
  }

  const xAt = (idx) =>
    points.length === 1 ? padL + plotW / 2 : padL + (idx / (points.length - 1)) * plotW;
  const yAt = (v) => {
    const clamped = Math.max(yMin, Math.min(yMax, Number(v ?? 0)));
    return padT + plotH - ((clamped - yMin) / (yMax - yMin)) * plotH;
  };

  // Build continuous valid segments so missing intervals don't draw misleading bridges
  const segments = [];
  let currentSeg = [];
  const missingMarkers = [];

  points.forEach((pt, idx) => {
    const cx = xAt(idx);
    const cy = yAt(pt.display_value);
    if (pt.is_valid) {
      currentSeg.push(`${cx.toFixed(1)},${cy.toFixed(1)}`);
    } else {
      if (currentSeg.length > 0) {
        segments.push(currentSeg);
        currentSeg = [];
      }
      missingMarkers.push({ cx, cy: yAt(0) });
    }
  });
  if (currentSeg.length > 0) {
    segments.push(currentSeg);
  }

  // Fallback full display line (including 0s during disconnect) in faint dashed slate
  const fullDisplayPath = points
    .map((pt, idx) => `${xAt(idx).toFixed(1)},${yAt(pt.display_value).toFixed(1)}`)
    .join(' ');

  const yMid = (yMin + yMax) / 2;
  const firstTs = points[0]?.timestamp_ms;
  const lastTs = points[points.length - 1]?.timestamp_ms;

  return (
    <div className="chart-box">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: '100%', height: `${height}px`, display: 'block' }}
        role="img"
        aria-label="Sensor reading over time"
      >
        {/* Grid lines */}
        {[yMin, yMid, yMax].map((tickVal, i) => {
          const y = yAt(tickVal);
          return (
            <g key={i}>
              <line
                x1={padL}
                y1={y}
                x2={width - padR}
                y2={y}
                stroke="#e2e8f0"
                strokeDasharray={i === 1 ? '3,3' : undefined}
                strokeWidth="1"
              />
              <text
                x={padL - 5}
                y={y + 3}
                textAnchor="end"
                fontSize="10"
                fill="#64748b"
              >
                {isDigital ? (i === 0 ? '0' : i === 2 ? '1' : '') : tickVal.toFixed(0)}
              </text>
            </g>
          );
        })}

        {/* Dashed zero-drop trace across missing intervals */}
        {missingMarkers.length > 0 && (
          <polyline
            fill="none"
            stroke="#94a3b8"
            strokeWidth="1.2"
            strokeDasharray="3,3"
            points={fullDisplayPath}
          />
        )}

        {/* Solid blue polyline for valid readings */}
        {segments.map((seg, i) =>
          seg.length > 1 ? (
            <polyline
              key={i}
              fill="none"
              stroke="#1d4ed8"
              strokeWidth="2"
              strokeLinejoin="round"
              strokeLinecap="round"
              points={seg.join(' ')}
            />
          ) : (
            <circle
              key={i}
              cx={seg[0].split(',')[0]}
              cy={seg[0].split(',')[1]}
              r="2.6"
              fill="#1d4ed8"
            />
          )
        )}

        {/* Missing-data markers at 0 */}
        {missingMarkers.map((m, idx) => (
          <circle key={idx} cx={m.cx} cy={m.cy} r="2.3" fill="#dc2626" />
        ))}

        {/* Time labels */}
        <text x={padL} y={height - 5} fontSize="10" fill="#64748b">
          {formatClock(firstTs)}
        </text>
        <text x={width - padR} y={height - 5} textAnchor="end" fontSize="10" fill="#64748b">
          {formatClock(lastTs)} {unit ? `(${unit})` : ''}
        </text>
      </svg>
    </div>
  );
}
