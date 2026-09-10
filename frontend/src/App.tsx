import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity, BarChart3, BellRing, Boxes, ChevronRight, CircleDot, Gauge, GitBranch,
  Menu, Plus, Radar, RefreshCw, Settings, ShieldCheck, Siren, Sparkles,
} from "lucide-react";
import { api } from "./api";
import { FaultModal } from "./components/FaultModal";
import { EvalModal } from "./components/EvalModal";
import { IncidentDetail } from "./components/IncidentDetail";
import { IncidentList } from "./components/IncidentList";
import type { DashboardStats, EvalReport, HoldoutReport, Incident, IncidentEvent, ObservabilityMode, ObservabilityStatus, Scenario, SkillCondition, SkillVersion } from "./types";

const emptyStats: DashboardStats = {
  total_incidents: 0, active_incidents: 0, awaiting_approval: 0,
  critical_incidents: 0, resolved_incidents: 0, average_mttr_seconds: 0,
};

const streamable = new Set(["queued", "investigating", "remediating"]);

export default function App() {
  const [stats, setStats] = useState(emptyStats);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selected, setSelected] = useState<Incident | null>(null);
  const [events, setEvents] = useState<IncidentEvent[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [evalOpen, setEvalOpen] = useState(false);
  const [evalReport, setEvalReport] = useState<EvalReport | null>(null);
  const [evalRunning, setEvalRunning] = useState(false);
  const [lifecycleRunning, setLifecycleRunning] = useState(false);
  const [evalError, setEvalError] = useState<string | null>(null);
  const [observability, setObservability] = useState<ObservabilityStatus | null>(null);
  const [activeSkill, setActiveSkill] = useState<SkillVersion | null>(null);
  const [skillVersions, setSkillVersions] = useState<SkillVersion[]>([]);
  const [holdout, setHoldout] = useState<HoldoutReport | null>(null);

  const refreshDashboard = useCallback(async () => {
    try {
      const [nextStats, nextIncidents] = await Promise.all([api.stats(), api.incidents()]);
      setStats(nextStats);
      setIncidents(nextIncidents);
      setSelected((current) => {
        if (!current) return nextIncidents[0] ?? null;
        return nextIncidents.find((item) => item.id === current.id) ?? nextIncidents[0] ?? null;
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load incidents");
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshSelected = useCallback(async (id: string) => {
    const [incident, nextEvents] = await Promise.all([api.incident(id), api.events(id)]);
    setSelected(incident);
    setEvents(nextEvents);
    setIncidents((current) => current.map((item) => item.id === incident.id ? incident : item));
    setStats(await api.stats());
  }, []);

  useEffect(() => {
    Promise.all([refreshDashboard(), api.scenarios().then(setScenarios)]).catch(() => undefined);
    Promise.all([
      api.observabilityStatus().then(setObservability),
      api.activeSkill().then(setActiveSkill),
      api.skillVersions().then(setSkillVersions),
      api.latestHoldout().then(setHoldout).catch(() => undefined),
    ]).catch(() => undefined);
  }, [refreshDashboard]);

  useEffect(() => {
    if (!selected) {
      setEvents([]);
      return;
    }
    api.events(selected.id).then(setEvents).catch(() => undefined);
  }, [selected?.id]);

  useEffect(() => {
    if (!selected || !streamable.has(selected.status)) return;
    const source = new EventSource(`/api/incidents/${selected.id}/events/stream`);
    source.onmessage = () => refreshSelected(selected.id).catch(() => undefined);
    source.onerror = () => source.close();
    const fallback = window.setInterval(
      () => refreshSelected(selected.id).catch(() => undefined), 700,
    );
    return () => { source.close(); window.clearInterval(fallback); };
  }, [selected?.id, selected?.status, refreshSelected]);

  const chooseIncident = async (incident: Incident) => {
    setSelected(incident);
    setSidebarOpen(false);
    try { await refreshSelected(incident.id); } catch { /* list snapshot remains usable */ }
  };

  const inject = async (scenario: Scenario["key"], reporter: string, skill: SkillCondition, mode: ObservabilityMode) => {
    try {
      const incident = await api.inject(scenario, reporter, skill, mode);
      setModalOpen(false);
      setSelected(incident);
      setIncidents((current) => [incident, ...current]);
      setEvents([]);
      await refreshDashboard();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Fault injection failed");
    }
  };

  const control = async (action: "pause" | "resume") => {
    if (!selected) return;
    try {
      const reason = action === "pause"
        ? "Operator requested a safe-boundary investigation checkpoint."
        : "Operator reviewed persisted progress and requested continuation.";
      const updated = await api[action](selected.id, "Li Ming", reason);
      setSelected(updated);
      await refreshSelected(selected.id);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Workflow control failed");
    }
  };

  const runEval = async () => {
    setEvalRunning(true);
    setEvalError(null);
    try { setEvalReport(await api.runEval()); }
    catch (failure) { setEvalError(failure instanceof Error ? failure.message : "Evaluation failed"); }
    finally { setEvalRunning(false); }
  };

  const refreshSkills = async () => {
    const [nextActive, nextVersions] = await Promise.all([api.activeSkill(), api.skillVersions()]);
    setActiveSkill(nextActive);
    setSkillVersions(nextVersions);
  };

  const evolveSkill = async () => {
    setLifecycleRunning(true); setEvalError(null);
    try {
      const result = await api.evolveSkill("Li Ming", "Promote only after the frozen holdout and safety gates pass.");
      setHoldout(result.report); setActiveSkill(result.active); await refreshSkills();
    } catch (failure) { setEvalError(failure instanceof Error ? failure.message : "Skill evolution failed"); }
    finally { setLifecycleRunning(false); }
  };

  const rollbackSkill = async () => {
    setLifecycleRunning(true); setEvalError(null);
    try {
      setActiveSkill(await api.rollbackSkill("Li Ming", "Operator requested audited rollback to the previous accepted snapshot."));
      await refreshSkills();
    } catch (failure) { setEvalError(failure instanceof Error ? failure.message : "Skill rollback failed"); }
    finally { setLifecycleRunning(false); }
  };

  const decide = async (action: "approve" | "reject", actor: string, reason: string) => {
    if (!selected) return;
    try {
      const updated = await api.decide(selected.id, action, actor, reason);
      setSelected(updated);
      await refreshSelected(selected.id);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Decision failed");
      throw failure;
    }
  };

  const kpis = useMemo(() => [
    { label: "Active incidents", value: stats.active_incidents, icon: Siren, tone: "danger" },
    { label: "Awaiting approval", value: stats.awaiting_approval, icon: ShieldCheck, tone: "warning" },
    { label: "Critical open", value: stats.critical_incidents, icon: BellRing, tone: "critical" },
    { label: "Resolved", value: stats.resolved_incidents, icon: CircleDot, tone: "success" },
    { label: "Mean recovery", value: stats.average_mttr_seconds ? `${stats.average_mttr_seconds.toFixed(1)}s` : "—", icon: Gauge, tone: "neutral" },
  ], [stats]);

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? "sidebar-open" : ""}`}>
        <div className="brand">
          <div className="brand-mark"><Radar size={21} /></div>
          <div><strong>EvoSRE</strong><span>Self-evolving SRE agent</span></div>
        </div>
        <nav className="nav-stack">
          <button className="nav-item active"><Activity size={18} /><span>Command center</span></button>
          <button className="nav-item"><Boxes size={18} /><span>Scenarios</span><em>{scenarios.length}</em></button>
          <button className="nav-item"><GitBranch size={18} /><span>Deployments</span></button>
          <button className="nav-item"><Sparkles size={18} /><span>Agent runs</span></button>
        </nav>
        <div className="sidebar-spacer" />
        <div className="environment-card">
          <span><i className={observability?.available ? "" : "offline"} /> Observability fabric</span>
          <strong>{observability?.available ? "Real OTel / LGTM online" : "Fixture mode ready"}</strong>
          <small>{observability?.available ? "Prometheus · Loki · Tempo" : "Start Docker stack for live signals"}</small>
        </div>
        <button className="nav-item"><Settings size={18} /><span>Settings</span></button>
        <div className="operator">
          <div className="avatar">LM</div>
          <div><strong>Li Ming</strong><span>On-call engineer</span></div>
          <ChevronRight size={16} />
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <button className="mobile-menu" onClick={() => setSidebarOpen((value) => !value)}><Menu /></button>
          <div>
            <p className="eyebrow"><span className="live-dot" /> LIVE OPERATIONS</p>
            <h1>Incident command center</h1>
            <p>Investigate symptoms, review evidence and approve safe recovery actions.</p>
          </div>
          <div className="topbar-actions">
            <button className="secondary-button eval-button" onClick={() => { setEvalOpen(true); if (!evalReport) void runEval(); }}><BarChart3 size={17} /> Skill eval</button>
            <button className="icon-button" onClick={refreshDashboard} aria-label="Refresh"><RefreshCw size={18} /></button>
            <button className="primary-button" onClick={() => setModalOpen(true)}><Plus size={18} /> Inject test fault</button>
          </div>
        </header>

        {error && <div className="error-banner"><span>{error}</span><button onClick={() => setError(null)}>Dismiss</button></div>}

        <section className="kpi-grid">
          {kpis.map(({ label, value, icon: Icon, tone }) => (
            <article className={`kpi-card ${tone}`} key={label}>
              <div className="kpi-icon"><Icon size={18} /></div>
              <div><span>{label}</span><strong>{value}</strong></div>
            </article>
          ))}
        </section>

        <section className="workspace-grid">
          <IncidentList incidents={incidents} selectedId={selected?.id} loading={loading} onSelect={chooseIncident} onInject={() => setModalOpen(true)} />
          <IncidentDetail incident={selected} events={events} onDecide={decide} onControl={control} />
        </section>
      </main>

      {modalOpen && <FaultModal scenarios={scenarios} observability={observability} activeSkill={activeSkill} onClose={() => setModalOpen(false)} onInject={inject} />}
      {evalOpen && <EvalModal legacyReport={evalReport} holdout={holdout} active={activeSkill} versions={skillVersions} running={evalRunning} lifecycleRunning={lifecycleRunning} error={evalError} onRunLegacy={runEval} onEvolve={evolveSkill} onRollback={rollbackSkill} onClose={() => setEvalOpen(false)} />}
    </div>
  );
}
