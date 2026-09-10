import { Activity, Database, GitPullRequest, ServerCrash, X, Zap } from "lucide-react";
import { useState } from "react";
import type { ObservabilityMode, ObservabilityStatus, Scenario, ScenarioKey, SkillCondition, SkillVersion } from "../types";

const scenarioIcons: Partial<Record<ScenarioKey, typeof Activity>> = {
  db_pool_exhaustion: Database,
  payment_timeout: Activity,
  bad_deployment: GitPullRequest,
  cache_outage: ServerCrash,
};

export function FaultModal({ scenarios, observability, activeSkill, onClose, onInject }: {
  scenarios: Scenario[];
  observability: ObservabilityStatus | null;
  activeSkill: SkillVersion | null;
  onClose: () => void;
  onInject: (scenario: ScenarioKey, reporter: string, skill: SkillCondition, mode: ObservabilityMode) => Promise<void>;
}) {
  const liveScenarios = new Set<ScenarioKey>(["bad_deployment", "db_pool_exhaustion", "cache_outage", "payment_timeout"]);
  const [selected, setSelected] = useState<ScenarioKey>(scenarios[0]?.key ?? "bad_deployment");
  const [reporter, setReporter] = useState("Li Ming");
  const [skill, setSkill] = useState<SkillCondition>("evolved");
  const [mode, setMode] = useState<ObservabilityMode>("fixture");
  const [submitting, setSubmitting] = useState(false);
  const submit = async () => {
    setSubmitting(true);
    try { await onInject(selected, reporter, skill, mode); } finally { setSubmitting(false); }
  };

  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="fault-modal" role="dialog" aria-modal="true">
        <div className="modal-heading">
          <div><span className="section-kicker">SAFE FAULT LAB</span><h2>Inject a test incident</h2><p>Choose a deterministic scenario. No host or cloud resources are changed.</p></div>
          <button className="icon-button" onClick={onClose}><X size={19} /></button>
        </div>
        <div className="scenario-grid">
          {scenarios.map((scenario) => {
            const Icon = scenarioIcons[scenario.key] ?? Activity;
            const disabled = mode === "otel" && !liveScenarios.has(scenario.key);
            return (
              <button disabled={disabled} className={`scenario-card ${selected === scenario.key ? "selected" : ""}`} onClick={() => setSelected(scenario.key)} key={scenario.key}>
                <div className="scenario-icon"><Icon size={20} /></div>
                <span className={`severity-badge ${scenario.severity}`}><i />{scenario.severity}</span>
                <strong>{scenario.name}</strong>
                <p>{scenario.description}</p>
                <code>{scenario.signal}</code>
              </button>
            );
          })}
        </div>
        <div className="fault-options">
          <label className="form-field"><span>Incident reporter</span><input value={reporter} onChange={(event) => setReporter(event.target.value)} /></label>
          <label className="form-field"><span>Telemetry source</span><select value={mode} onChange={(event) => { const next = event.target.value as ObservabilityMode; setMode(next); if (next === "otel") setSelected("bad_deployment"); }}><option value="fixture">Deterministic fixture</option><option value="otel" disabled={!observability?.available}>Real OTel / LGTM{observability?.available ? "" : " (offline)"}</option></select></label>
          <label className="form-field"><span>Diagnostic skill</span><select value={skill} onChange={(event) => setSkill(event.target.value as SkillCondition)}><option value="evolved">Active: {activeSkill?.version ?? "evolved skill"}</option><option value="static">Static skill</option><option value="none">No skill baseline</option></select></label>
        </div>
        <div className="modal-footer">
          <div><Zap size={16} /><span>{mode === "otel" ? "Signals are exported over OTLP and queried from Prometheus, Loki and Tempo." : "Agent investigation starts automatically after deterministic injection."}</span></div>
          <button className="secondary-button" onClick={onClose}>Cancel</button>
          <button className="primary-button" disabled={submitting || reporter.trim().length < 2} onClick={submit}>{submitting ? <span className="spinner" /> : <Zap size={17} />} Inject scenario</button>
        </div>
      </div>
    </div>
  );
}
