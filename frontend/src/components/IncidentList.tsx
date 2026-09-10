import { AlertTriangle, CheckCircle2, ChevronRight, Clock3, Search, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import type { Incident, IncidentStatus } from "../types";

const labels: Record<IncidentStatus, string> = {
  queued: "Queued", investigating: "Investigating", awaiting_approval: "Needs approval",
  remediating: "Remediating", paused: "Paused", resolved: "Resolved", dismissed: "Dismissed", failed: "Failed",
};

function ago(value: string) {
  const seconds = Math.max(1, Math.round((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes}m ago` : `${Math.round(minutes / 60)}h ago`;
}

export function IncidentList({ incidents, selectedId, loading, onSelect, onInject }: {
  incidents: Incident[];
  selectedId?: string;
  loading: boolean;
  onSelect: (incident: Incident) => void;
  onInject: () => void;
}) {
  const [query, setQuery] = useState("");
  const visible = useMemo(() => {
    const needle = query.toLowerCase().trim();
    if (!needle) return incidents;
    return incidents.filter((item) => `${item.title} ${item.service} ${item.status}`.toLowerCase().includes(needle));
  }, [incidents, query]);

  return (
    <aside className="incident-panel panel">
      <div className="panel-heading">
        <div><span className="section-kicker">INCIDENT QUEUE</span><h2>Open investigations</h2></div>
        <span className="count-pill">{incidents.length}</span>
      </div>
      <label className="search-field"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search service or incident" /></label>
      <div className="incident-list">
        {loading && <div className="list-message"><span className="spinner" />Loading incident state…</div>}
        {!loading && !visible.length && (
          <div className="empty-list">
            <div><Sparkles size={22} /></div>
            <strong>No incidents in the queue</strong>
            <p>Inject a safe test fault and watch the Agent investigate it.</p>
            <button onClick={onInject}>Start a simulation</button>
          </div>
        )}
        {visible.map((incident) => {
          const active = incident.id === selectedId;
          const resolved = incident.status === "resolved";
          return (
            <button className={`incident-row ${active ? "selected" : ""}`} onClick={() => onSelect(incident)} key={incident.id}>
              <div className="incident-row-top">
                <span className={`severity-badge ${incident.severity}`}><i />{incident.severity}</span>
                <span className={`status-text ${incident.status}`}>{resolved ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}{labels[incident.status]}</span>
              </div>
              <strong>{incident.title}</strong>
              <p>{incident.service} · {String(incident.metadata.alert_signal ?? "Signal pending")}</p>
              <div className="incident-row-foot"><span><Clock3 size={13} />{ago(incident.created_at)}</span><code>#{incident.id.slice(0, 7)}</code><ChevronRight size={15} /></div>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
