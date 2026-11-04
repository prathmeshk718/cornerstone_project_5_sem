import React, { useState } from 'react';

export default function SimulatorToolbar({ simulator, onSelectScenario }) {
  const [expanded, setExpanded] = useState(true);
  if (!simulator) return null;

  const scenarios = simulator.available_scenarios || [];

  return (
    <section className="panel" aria-label="Hardware and Fault Simulation Controls">
      <div className="panel-header" style={{ marginBottom: expanded ? '8px' : 0 }}>
        <div>
          <h2 className="panel-title">
            Hardware &amp; Fault Verification Mode:{' '}
            <span style={{ color: '#1d4ed8' }}>{simulator.label}</span>
          </h2>
          <div style={{ fontSize: '0.77rem', color: '#64748b' }}>
            {simulator.description}
          </div>
        </div>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? 'Hide Scenarios' : 'Show Scenarios'}
        </button>
      </div>

      {expanded && (
        <div className="sim-grid">
          {scenarios.map((sc) => (
            <button
              key={sc.id}
              type="button"
              className={`sim-btn ${simulator.active_scenario === sc.id ? 'active' : ''}`}
              onClick={() => onSelectScenario(sc.id)}
              title={sc.description}
            >
              {sc.label}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
