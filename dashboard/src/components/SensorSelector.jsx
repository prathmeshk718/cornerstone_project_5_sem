import React, { useState } from 'react';

const ORDERED_IDS = [
  'ds18b20_temp',
  'flame_sensor',
  'dht11_temp',
  'dht11_humidity',
];

export default function SensorSelector({
  sensorCatalog = {},
  activeSensorIds = [],
  onAddSensors,
  onRemoveSensor,
  onClearAll,
  onInspectSensor,
}) {
  const [checkedIds, setCheckedIds] = useState([]);

  const toggleCheck = (sid) => {
    setCheckedIds((prev) =>
      prev.includes(sid) ? prev.filter((x) => x !== sid) : [...prev, sid]
    );
  };

  const handleAddChecked = () => {
    const toAdd = checkedIds.filter((sid) => !activeSensorIds.includes(sid));
    if (toAdd.length > 0) {
      onAddSensors(toAdd);
    }
    setCheckedIds([]);
  };

  const handleAddAll = () => {
    onAddSensors(ORDERED_IDS);
    setCheckedIds([]);
  };

  const unaddedSelectedCount = checkedIds.filter(
    (sid) => !activeSensorIds.includes(sid)
  ).length;

  return (
    <section className="panel" aria-label="Sensor Selection">
      <div className="panel-header">
        <div>
          <h2 className="panel-title">Sensor Selection</h2>
          <div style={{ fontSize: '0.78rem', color: '#64748b' }}>
            Select one or multiple sensors to add to the dashboard. Background data storage runs continuously for all sensors regardless of selection.
          </div>
        </div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <button
            type="button"
            className="btn btn-primary"
            disabled={unaddedSelectedCount === 0}
            onClick={handleAddChecked}
          >
            Add Selected ({unaddedSelectedCount})
          </button>
          <button
            type="button"
            className="btn"
            disabled={activeSensorIds.length === ORDERED_IDS.length}
            onClick={handleAddAll}
          >
            Add All Sensors
          </button>
          {activeSensorIds.length > 0 && (
            <button
              type="button"
              className="btn btn-danger-outline"
              onClick={onClearAll}
            >
              Remove All from View
            </button>
          )}
        </div>
      </div>

      <div className="selector-grid">
        {ORDERED_IDS.map((sid) => {
          const meta = sensorCatalog[sid] || { name: sid, unit: '' };
          const isAdded = activeSensorIds.includes(sid);
          const isChecked = checkedIds.includes(sid);

          return (
            <div
              key={sid}
              className={`selector-option ${isAdded ? 'active-on-dash' : ''}`}
            >
              <label>
                <input
                  type="checkbox"
                  checked={isAdded || isChecked}
                  disabled={isAdded}
                  onChange={() => toggleCheck(sid)}
                />
                <span>{meta.name}</span>
              </label>

              <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => onInspectSensor(sid)}
                  title="View individual sensor hardware & analytics info"
                >
                  Info
                </button>
                {isAdded ? (
                  <button
                    type="button"
                    className="btn btn-sm btn-danger-outline"
                    onClick={() => onRemoveSensor(sid)}
                  >
                    Remove
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn btn-sm btn-primary"
                    onClick={() => onAddSensors([sid])}
                  >
                    Add
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
