import React, { useState } from 'react';

const EDITABLE_FIELDS = [
  { key: 'arduino_timeout_sec', label: 'Arduino Heartbeat Timeout (s)', step: '0.5' },
  { key: 'sensor_timeout_sec', label: 'Individual Sensor Timeout (s)', step: '0.5' },
  { key: 'ds18b20_stuck_warn_sec', label: 'DS18B20 Constant Warning Window (s)', step: '1' },
  { key: 'ds18b20_stuck_maint_sec', label: 'DS18B20 Constant Maintenance Window (s)', step: '1' },
  { key: 'dht11_temp_stuck_warn_sec', label: 'DHT11 Temp Constant Warning (s)', step: '1' },
  { key: 'dht11_hum_stuck_warn_sec', label: 'DHT11 Humidity Constant Warning (s)', step: '1' },
  { key: 'flame_validation_window_sec', label: 'Flame Cross-Validation Window (s)', step: '0.5' },
  { key: 'fire_temp_rise_threshold_c', label: 'Fire Corroboration Temp Rise (°C)', step: '0.5' },
  { key: 'max_temp_sensor_divergence_c', label: 'Max DS18B20 vs DHT11 Divergence (°C)', step: '0.5' },
  { key: 'ds18b20_max_step_c', label: 'DS18B20 Max Expected Step (°C)', step: '0.5' },
  { key: 'dht11_temp_max_step_c', label: 'DHT11 Temp Max Expected Step (°C)', step: '0.5' },
  { key: 'dht11_hum_max_step_pct', label: 'DHT11 Humidity Max Expected Step (%)', step: '1' },
];

export default function ThresholdsModal({ thresholds, onSave, onClose }) {
  const [draft, setDraft] = useState({ ...(thresholds || {}) });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  if (!thresholds) return null;

  const handleChange = (key, val) => {
    setDraft((prev) => ({ ...prev, [key]: Number(val) }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      const resp = await fetch('/api/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft),
      });
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || 'Failed to save configuration');
      }
      const data = await resp.json();
      onSave(data.thresholds);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="panel-header">
          <h2 className="panel-title">Configurable Condition Monitoring Thresholds</h2>
          <button type="button" className="btn btn-sm" onClick={onClose}>
            Close
          </button>
        </div>
        <div style={{ fontSize: '0.78rem', color: '#64748b', marginBottom: '10px' }}>
          All three analysis methods use configurable windows and physical variation thresholds persisted on the backend.
        </div>

        <form onSubmit={handleSubmit}>
          <div className="form-grid">
            {EDITABLE_FIELDS.map((f) => (
              <div className="form-field" key={f.key}>
                <label>{f.label}</label>
                <input
                  type="number"
                  step={f.step}
                  value={draft[f.key] ?? ''}
                  onChange={(e) => handleChange(f.key, e.target.value)}
                />
              </div>
            ))}
          </div>
          {error && (
            <div style={{ color: '#b91c1c', fontSize: '0.8rem', marginBottom: '8px' }}>
              {error}
            </div>
          )}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? 'Saving...' : 'Save Thresholds'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
