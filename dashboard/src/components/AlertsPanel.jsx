import React, { useState } from 'react';

function domainBadge(domain, severity) {
  if (domain === 'environmental') {
    return <span className="badge badge-crit">ENVIRONMENTAL HAZARD</span>;
  }
  if (domain === 'connectivity') {
    return <span className="badge badge-off">CONNECTIVITY</span>;
  }
  return (
    <span className={severity === 'critical' ? 'badge badge-crit' : 'badge badge-warn'}>
      SENSOR MAINTENANCE
    </span>
  );
}

export default function AlertsPanel({ alerts = [] }) {
  const [filterDomain, setFilterDomain] = useState('all');

  const filtered = alerts.filter((a) =>
    filterDomain === 'all' ? true : a.alert_domain === filterDomain
  );

  return (
    <section className="panel" aria-label="Real-Time and Historical Alerts">
      <div className="panel-header">
        <div>
          <h2 className="panel-title">Real-Time System &amp; Maintenance Alerts ({filtered.length})</h2>
          <div style={{ fontSize: '0.77rem', color: '#64748b' }}>
            Environmental fire hazards and sensor-maintenance warnings are strictly separated and persisted in SQLite.
          </div>
        </div>
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          {[
            { id: 'all', label: 'All' },
            { id: 'environmental', label: 'Environmental (Fire)' },
            { id: 'sensor_health', label: 'Sensor Health' },
            { id: 'connectivity', label: 'Connectivity' },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`btn btn-sm ${filterDomain === tab.id ? 'btn-primary' : ''}`}
              onClick={() => setFilterDomain(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        <div style={{ fontSize: '0.83rem', color: '#64748b', padding: '12px 4px' }}>
          No alerts recorded for the selected category. All monitored channels are within normal bounds.
        </div>
      ) : (
        <div className="alerts-table-wrap">
          <table className="alerts-table">
            <thead>
              <tr>
                <th style={{ width: '165px' }}>Timestamp</th>
                <th style={{ width: '175px' }}>Type</th>
                <th style={{ width: '160px' }}>Sensor</th>
                <th style={{ width: '210px' }}>Category / Warning</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((al, idx) => (
                <tr key={al.id || `${al.timestamp_ms}-${idx}`}>
                  <td style={{ whiteSpace: 'nowrap', color: '#475569' }}>
                    {new Date(al.timestamp_ms).toLocaleString()}
                  </td>
                  <td>{domainBadge(al.alert_domain, al.severity)}</td>
                  <td style={{ fontWeight: 600 }}>{al.sensor_name}</td>
                  <td style={{ fontWeight: 600 }}>
                    <div>{al.alert_category}</div>
                    <div style={{ fontSize: '0.75rem', color: '#475569', fontWeight: 500 }}>
                      {al.title}
                    </div>
                  </td>
                  <td>{al.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
