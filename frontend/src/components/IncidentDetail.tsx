import { useEffect, useMemo, useState } from "react";
import {
  Activity, AlertOctagon, ArrowDown, ArrowRight, Bot, Check, CheckCircle2,
  ChevronDown, CircleDot, Clock3, Code2, Database, FileCode2, FileText,
  Gauge, GitCommitHorizontal, Network, PauseCircle, Play, RotateCw, Search,
  ShieldAlert, ShieldCheck, Sparkles, TerminalSquare, UserCheck, XCircle,
} from "lucide-react";
import type { Evidence, Incident, IncidentEvent, IncidentStatus } from "../types";

type Tab = "diagnosis" | "evidence" | "trace";

const statusLabels: Record<IncidentStatus, string> = {
  queued: "Queued", investigating: "Agent investigating", awaiting_approval: "Action awaiting approval",
  remediating: "Executing remediation", paused: "Workflow paused", resolved: "Recovery verified",
  dismissed: "Action rejected", failed: "Workflow failed",
};

const evidenceIcons = {
  metric: Gauge, log: TerminalSquare, trace: Activity, dependency: Network,
  deployment: GitCommitHorizontal, runbook: FileText,
};

function timestamp(value: string) {
  return new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));
}

function label(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function MetricEvidence({ evidence }: { evidence: Evidence }) {
  return (
    <div className="metric-evidence-grid">
      {Object.entries(evidence.payload).filter(([, raw]) => typeof raw === "object" && raw !== null && "value" in raw && "healthy_threshold" in raw).map(([name, raw]) => {
        const metric = raw as { value: number; unit: string; healthy_threshold: number };
        const breached = metric.value > metric.healthy_threshold;
        return (
          <div className={`metric-tile ${breached ? "breached" : "healthy"}`} key={name}>
            <span>{label(name)}</span><strong>{metric.value}<small>{metric.unit}</small></strong>
            <em>{breached ? "Outside envelope" : "Healthy"} · ≤ {metric.healthy_threshold}{metric.unit}</em>
          </div>
        );
      })}
    </div>
  );
}

function EvidenceBody({ evidence }: { evidence: Evidence }) {
  if (evidence.kind === "metric") return <MetricEvidence evidence={evidence} />;
  if (evidence.kind === "log") {
    const entries = (evidence.payload.entries ?? []) as Array<{ level: string; message: string }>;
    return <div className="log-viewer">{entries.map((entry, index) => <div key={index}><span className={entry.level.toLowerCase()}>{entry.level}</span><code>{entry.message}</code></div>)}</div>;
  }
  if (evidence.kind === "trace") {
    const spans = (evidence.payload.spans ?? []) as Array<{ service: string; operation: string; duration_ms: number; status: string; attributes?: Record<string, unknown> }>;
    return <div className="span-waterfall">{spans.map((span, index) => <div key={`${span.service}-${span.operation}-${index}`}><span className={`health-dot ${span.status === "error" ? "unhealthy" : "healthy"}`} /><strong>{span.service}</strong><code>{span.operation}</code><em>{span.duration_ms} ms</em>{span.attributes && <small>{Object.entries(span.attributes).map(([key, value]) => `${key}=${String(value)}`).join(" · ")}</small>}</div>)}</div>;
  }
  if (evidence.kind === "dependency") {
    const items = (evidence.payload.dependencies ?? []) as Array<{ name: string; status: string; latency_ms: number; error_rate: number; detail: string }>;
    return <div className="dependency-grid">{items.map((item) => <div key={item.name}><span className={`health-dot ${item.status}`} /><strong>{item.name}</strong><em>{item.status}</em><p>{item.detail}</p><small>{item.latency_ms} ms · {item.error_rate}% errors</small></div>)}</div>;
  }
  if (evidence.kind === "runbook") {
    const steps = (evidence.payload.steps ?? []) as string[];
    return <ol className="runbook-list">{steps.map((step, index) => <li key={step}><span>{index + 1}</span><p>{step}</p></li>)}</ol>;
  }
  return <div className="deployment-grid">{Object.entries(evidence.payload).map(([key, value]) => <div key={key}><span>{label(key)}</span><strong>{String(value)}</strong></div>)}</div>;
}

function EvidenceCard({ evidence }: { evidence: Evidence }) {
  const [open, setOpen] = useState(true);
  const Icon = evidenceIcons[evidence.kind];
  return (
    <article className="evidence-card">
      <button className="evidence-heading" onClick={() => setOpen((value) => !value)}>
        <div className={`evidence-kind ${evidence.kind}`}><Icon size={17} /></div>
        <div><span>{evidence.kind} evidence</span><strong>{evidence.title}</strong><p>{evidence.summary}</p></div>
        <code>{evidence.source.split("?")[0]}</code><ChevronDown className={open ? "rotated" : ""} size={18} />
      </button>
      {open && <div className="evidence-body"><EvidenceBody evidence={evidence} /></div>}
    </article>
  );
}

function EmptyDetail() {
  return (
    <section className="detail-panel panel empty-detail">
      <div className="empty-orbit"><RadarArtwork /></div>
      <span className="section-kicker">EVIDENCE-FIRST OPERATIONS</span>
      <h2>Choose an incident to begin</h2>
      <p>EvoSRE follows a controlled response loop with cited telemetry, durable checkpoints and an operator approval boundary.</p>
      <div className="workflow-strip">
        {[{ icon: Activity, label: "Detect" }, { icon: Search, label: "Investigate" }, { icon: Bot, label: "Diagnose" }, { icon: UserCheck, label: "Approve" }, { icon: CheckCircle2, label: "Verify" }].map(({ icon: Icon, label: text }, index) => (
          <div key={text}><span><Icon size={17} /></span><strong>{text}</strong>{index < 4 && <ArrowRight size={15} />}</div>
        ))}
      </div>
    </section>
  );
}

function RadarArtwork() {
  return <div className="radar-art"><div /><div /><div /><i /><Sparkles size={24} /></div>;
}

function Investigating({ incident, events }: { incident: Incident; events: IncidentEvent[] }) {
  const tools = events.filter((event) => event.kind === "tool");
  const steps = ["Query service metrics", "Correlate application logs", "Query distributed traces", "Inspect dependencies", "Inspect deployments", "Read operator runbook"];
  return (
    <div className="investigating-state">
      <div className="agent-pulse"><Bot size={26} /><i /><i /><i /></div>
      <span className="section-kicker">{incident.status === "paused" ? "DURABLE CHECKPOINT" : "AGENT RUNNING"}</span>
      <h3>{incident.status === "paused" ? "Investigation paused safely" : `Investigating ${incident.service}`}</h3>
      <p>{incident.status === "paused" ? `${incident.evidence.length} evidence groups are persisted. Resume continues without repeating completed tools.` : "The Agent is selecting read-only tools and grounding every conclusion in the incident snapshot."}</p>
      <div className="investigation-steps">
        {steps.map((step, index) => <div className={index < tools.length ? "done" : index === tools.length ? "active" : ""} key={step}><span>{index < tools.length ? <Check size={15} /> : index + 1}</span><strong>{step}</strong>{index === tools.length && <em>running</em>}</div>)}
      </div>
    </div>
  );
}

function ApprovalPanel({ incident, onDecide }: {
  incident: Incident;
  onDecide: (action: "approve" | "reject", actor: string, reason: string) => Promise<void>;
}) {
  const [actor, setActor] = useState("Li Ming");
  const [reason, setReason] = useState("Evidence and rollback scope reviewed; proceed with the bounded recovery action.");
  const [submitting, setSubmitting] = useState<"approve" | "reject" | null>(null);
  useEffect(() => {
    setActor("Li Ming");
    setReason("Evidence and rollback scope reviewed; proceed with the bounded recovery action.");
    setSubmitting(null);
  }, [incident.id]);

  if (!incident.proposal) return null;
  const pending = incident.status === "awaiting_approval" && incident.proposal.status === "pending";
  const submit = async (action: "approve" | "reject") => {
    setSubmitting(action);
    try { await onDecide(action, actor, reason); } finally { setSubmitting(null); }
  };

  return (
    <aside className={`approval-panel ${pending ? "pending" : incident.proposal.status}`}>
      <div className="approval-title">
        <div>{pending ? <ShieldAlert size={20} /> : incident.proposal.status === "executed" ? <ShieldCheck size={20} /> : <XCircle size={20} />}</div>
        <div><span>{pending ? "OPERATOR APPROVAL REQUIRED" : "ACTION DECISION"}</span><h3>{label(incident.proposal.action)}</h3></div>
      </div>
      <p className="proposal-description">{incident.proposal.description}</p>
      <div className="action-facts"><div><span>Expected outcome</span><p>{incident.proposal.expected_outcome}</p></div><div><span>Risk boundary</span><p>{incident.proposal.risk}</p></div></div>
      {pending ? (
        <>
          <label className="form-field compact"><span>Approver</span><input value={actor} onChange={(event) => setActor(event.target.value)} /></label>
          <label className="form-field compact"><span>Decision reason</span><textarea rows={3} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
          <div className="decision-actions"><button className="reject-button" disabled={!!submitting || reason.trim().length < 8} onClick={() => submit("reject")}>Reject</button><button className="approve-button" disabled={!!submitting || actor.trim().length < 2 || reason.trim().length < 8} onClick={() => submit("approve")}>{submitting === "approve" ? <span className="spinner" /> : <Play size={16} />}Approve & execute</button></div>
          <small className="approval-note"><ShieldCheck size={13} />Only this incident's persisted FaultLab resource will change.</small>
        </>
      ) : (
        <div className="decision-record"><UserCheck size={17} /><div><span>{incident.proposal.status} by {incident.decision_actor ?? "operator"}</span><p>{incident.decision_reason}</p></div></div>
      )}
    </aside>
  );
}

export function IncidentDetail({ incident, events, onDecide, onControl }: {
  incident: Incident | null;
  events: IncidentEvent[];
  onDecide: (action: "approve" | "reject", actor: string, reason: string) => Promise<void>;
  onControl: (action: "pause" | "resume") => Promise<void>;
}) {
  const [tab, setTab] = useState<Tab>("diagnosis");
  useEffect(() => setTab("diagnosis"), [incident?.id]);
  const toolCount = useMemo(() => events.filter((event) => event.kind === "tool").length, [events]);
  if (!incident) return <EmptyDetail />;
  const inProgress = incident.status === "queued" || incident.status === "investigating" || incident.status === "paused";
  const confidence = incident.hypotheses[0] ? Math.round(incident.hypotheses[0].confidence * 100) : 0;

  return (
    <section className="detail-panel panel">
      <div className="incident-header">
        <div className="incident-breadcrumb"><span>INCIDENT</span><code>#{incident.id.slice(0, 7)}</code><span>·</span><span>{incident.service}</span></div>
        <div className="incident-title-row">
          <div className={`incident-symbol ${incident.severity}`}><AlertOctagon size={22} /></div>
          <div><h2>{incident.title}</h2><p>{String(incident.metadata.scenario_description ?? "Incident investigation")}</p></div>
          <span className={`detail-status ${incident.status}`}><i />{statusLabels[incident.status]}</span>
        </div>
        <div className="incident-context"><span><Clock3 size={14} />Started {timestamp(incident.created_at)}</span><span><UserCheck size={14} />Reported by {incident.reporter}</span><span><Code2 size={14} />{incident.scenario}</span><span><Activity size={14} />{incident.observability_mode === "otel" ? "Real OTel" : "Fixture"}</span><span><Sparkles size={14} />{String(incident.metadata.skill_version ?? incident.skill_condition)}</span>{(incident.status === "queued" || incident.status === "investigating") && <button className="workflow-control" onClick={() => void onControl("pause")}><PauseCircle size={14} />Pause</button>}{incident.status === "paused" && <button className="workflow-control resume" onClick={() => void onControl("resume")}><RotateCw size={14} />Resume</button>}</div>
      </div>

      <div className="tabs">
        <button className={tab === "diagnosis" ? "active" : ""} onClick={() => setTab("diagnosis")}>Diagnosis</button>
        <button className={tab === "evidence" ? "active" : ""} onClick={() => setTab("evidence")}>Evidence <span>{incident.evidence.length}</span></button>
        <button className={tab === "trace" ? "active" : ""} onClick={() => setTab("trace")}>Agent trace <span>{events.length}</span></button>
      </div>

      <div className="detail-scroll">
        {inProgress && <Investigating incident={incident} events={events} />}
        {!inProgress && tab === "diagnosis" && (
          <div className="diagnosis-layout">
            <div className="diagnosis-main">
              {incident.status === "resolved" && (
                <div className="recovery-banner"><CheckCircle2 size={22} /><div><span>RECOVERY VERIFIED</span><strong>All post-action health checks passed</strong><p>The Agent re-queried service telemetry after executing the approved action.</p></div></div>
              )}
              {incident.error && <div className="failure-banner"><XCircle size={19} /><div><strong>Workflow failed</strong><p>{incident.error}</p></div></div>}
              <article className="root-cause-card">
                <div className="root-cause-heading"><div><Bot size={19} /></div><span>AGENT DIAGNOSIS</span><em>{confidence}% confidence</em></div>
                <h3>{incident.hypotheses[0]?.cause ?? "Diagnosis pending"}</h3>
                <p>{incident.root_cause}</p>
                <div className="evidence-summary"><span><Database size={15} />{incident.evidence.length} evidence groups</span><span><TerminalSquare size={15} />{toolCount} tool calls</span><span><FileCode2 size={15} />Cited sources</span></div>
              </article>

              <div className="section-title"><div><span className="section-kicker">RANKED ANALYSIS</span><h3>Root-cause hypotheses</h3></div><span>Evidence IDs are immutable</span></div>
              <div className="hypothesis-list">
                {incident.hypotheses.map((hypothesis) => (
                  <article className={hypothesis.rank === 1 ? "leading" : ""} key={hypothesis.rank}>
                    <span className="rank">0{hypothesis.rank}</span>
                    <div className="hypothesis-copy"><strong>{hypothesis.cause}</strong><p>{hypothesis.rationale}</p><div className="confidence-bar"><i style={{ width: `${hypothesis.confidence * 100}%` }} /></div></div>
                    <em>{Math.round(hypothesis.confidence * 100)}%</em>
                  </article>
                ))}
              </div>

              {!!incident.recovery_checks.length && (
                <>
                  <div className="section-title"><div><span className="section-kicker">POST-ACTION PROBES</span><h3>Recovery checks</h3></div><span>{incident.recovery_checks.filter((item) => item.passed).length}/{incident.recovery_checks.length} passed</span></div>
                  <div className="recovery-grid">
                    {incident.recovery_checks.map((check) => <div key={check.name}><span><CheckCircle2 size={14} />{label(check.name)}</span><div><strong>{check.before}<small>{check.unit}</small></strong><ArrowDown size={15} /><strong>{check.after}<small>{check.unit}</small></strong></div></div>)}
                  </div>
                </>
              )}
            </div>
            <ApprovalPanel incident={incident} onDecide={onDecide} />
          </div>
        )}

        {!inProgress && tab === "evidence" && (
          <div className="evidence-stack">
            <div className="tab-intro"><div><span className="section-kicker">OBSERVABILITY SNAPSHOT</span><h3>Evidence collected by tools</h3></div><p>Every diagnosis claim must point back to these incident-scoped sources.</p></div>
            {incident.evidence.map((item) => <EvidenceCard evidence={item} key={item.id} />)}
          </div>
        )}

        {!inProgress && tab === "trace" && (
          <div className="trace-view">
            <div className="tab-intro"><div><span className="section-kicker">AUDIT TIMELINE</span><h3>Agent and operator activity</h3></div><p>Tool calls, decisions and recovery outcomes remain attributable.</p></div>
            <div className="trace-list">
              {events.map((event, index) => (
                <div className={`trace-event ${event.kind}`} key={event.id}>
                  <div className="trace-line"><span>{event.kind === "tool" ? <TerminalSquare size={15} /> : event.kind === "approval" ? <UserCheck size={15} /> : event.kind === "recovery" ? <CheckCircle2 size={15} /> : event.kind === "alert" ? <AlertOctagon size={15} /> : <Bot size={15} />}</span>{index < events.length - 1 && <i />}</div>
                  <div><div><span>{event.kind}</span><time>{timestamp(event.created_at)}</time></div><strong>{event.title}</strong><p>{event.detail}</p>{typeof event.payload.source === "string" && <code>{event.payload.source}</code>}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
