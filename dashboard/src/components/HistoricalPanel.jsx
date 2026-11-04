import React, { useEffect, useState } from 'react';
import SimpleLineChart from './SimpleLineChart';

export default function HistoricalPanel({ sensorCatalog = {}, onClose }) {
  const [selectedSensor, setSelectedSensor] = useState('ds18b20_temp');
  const [rangeMinutes, setRangeMinutes] = useState('10');
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd] = useState('');
  const [missingOnly, setMissingOnly] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  const loadHistory = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (rangeMinutes === 'custom') {
        if (customStart) params.set('start_ms', String(new Date(customStart).getTime()));
        if (customEnd) params.set('end_ms', String(new Date(customEnd).getTime()));
      } else {
        params.set('minutes', rangeMinutes);
      }
      if (missingOnly) params.set('missing_only', 'true');
      params.set('limit', '1500');
      params.set('max_points', '240');

      const resp = await fetch(`/api/history?${params.toString()}`);
      if (resp.ok) {
        const data = await resp.json();
        setResult(data);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadHistory();
  }, [rangeMinutes, missingOnly]);

  const meta = sensorCatalog[selectedSensor] || {};
  const chartPoints = (result?.points || []).map((p) => ({
    timestamp_ms: p.timestamp_ms,
    display_value: p[selectedSensor]?.display_value ?? 0,
    is_valid: Boolean(p[selectedSensor]?.is_valid),
  }));

  return (
    <section className="panel" aria-label="Historical SQLite Data Explorer">
      <div className="panel-header">
        <div>
          <h2 className="panel-title">Historical Database Retrieval &amp; Graph (SQLite)</h2>
          <div style={{ fontSize: '0.78rem', color: '#64748b' }}>
            Query persisted database records by timestamp. Missing readings (SQL NULL) are stored separately from genuine zero readings (0.0).
          </div>
        </div>
        <button type="button" className="btn btn-sm" onClick={onClose}>
          Close History View
        </button>
      </div>

      <div className="form-grid" style={{ alignItems: 'end' }}>
        <div className="form-field">
          <label>Sensor Channel</label>
          <select
            value={selectedSensor}
            onChange={(e) => setSelectedSensor(e.target.value)}
          >
            {Object.values(sensorCatalog).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} ({s.unit})
              </option>
            ))}
          </select>
        </div>

        <div className="form-field">
          <label>Time Window</label>
          <select
            value={rangeMinutes}
            onChange={(e) => setRangeMinutes(e.target.value)}
          >
            <option value="2">Last 2 Minutes</option>
            <option value="10">Last 10 Minutes</option>
            <option value="60">Last 1 Hour</option>
            <option value="360">Last 6 Hours</option>
            <option value="custom">Custom Timestamp Filter</option>
          </select>
        </div>

        {rangeMinutes === 'custom' && (
          <>
            <div className="form-field">
              <label>Start Timestamp</label>
              <input
                type="datetime-local"
                value={customStart}
                onChange={(e) => setCustomStart(e.target.value)}
              />
            </div>
            <div className="form-field">
              <label>End Timestamp</label>
              <input
                type="datetime-local"
                value={customEnd}
                onChange={(e) => setCustomEnd(e.target.value)}
              />
            </div>
          </>
        )}

        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <label style={{ fontSize: '0.8rem', display: 'flex', gap: '5px', alignItems: 'center' }}>
            <input
              type="checkbox"
              checked={missingOnly}
              onChange={(e) => setMissingOnly(e.target.checked)}
            />
            Missing/Invalid Only
          </label>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={loadHistory}
            disabled={loading}
          >
            {loading ? 'Querying...' : 'Refresh Query'}
          </button>
        </div>
      </div>

      {result && (
        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '10px', fontSize: '0.8rem' }}>
          <span className="badge badge-neutral">
            Matched DB Rows: {result.total_matching_rows}
          </span>
          <span className="badge badge-off">
            Missing / Disconnected (NULL) for {meta.name}: {result.missing_counts?.[selectedSensor] ?? 0}
          </span>
          <span className="badge badge-ok">
            Genuine Zero (0) Readings for {meta.name}: {result.genuine_zero_counts?.[selectedSensor] ?? 0}
          </span>
        </div>
      )}

      <SimpleLineChart
        points={chartPoints}
        unit={meta.unit}
        isDigital={Boolean(meta.is_digital_state)}
        height={165}
      />
    </section>
  );
}
