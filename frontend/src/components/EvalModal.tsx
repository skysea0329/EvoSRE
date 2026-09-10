import { BarChart3, CheckCircle2, FlaskConical, RotateCcw, ShieldCheck, Sparkles, X } from "lucide-react";
import type { EvalReport, HoldoutMetrics, HoldoutReport, SkillVersion } from "../types";

const conditionLabels = { none: "No skill", static: "Static skill", evolved: "Evolved skill" };
const pct = (value: number) => `${Math.round(value * 100)}%`;

function HoldoutCard({ title, metrics, candidate }: { title: string; metrics: HoldoutMetrics; candidate?: boolean }) {
  return (
    <article className={candidate ? "promoted" : ""}>
      <div><span>{title}</span>{candidate && <em><CheckCircle2 size={12} /> candidate</em>}</div>
      <strong>{pct(metrics.macro_score)}</strong>
      <div className="eval-bar"><i style={{ width: pct(metrics.macro_score) }} /></div>
      <dl>
        <div><dt>Known</dt><dd>{pct(metrics.known_accuracy)}</dd></div>
        <div><dt>OOD reject</dt><dd>{pct(metrics.ood_rejection_rate)}</dd></div>
        <div><dt>Injection</dt><dd>{pct(metrics.injection_resistance_rate)}</dd></div>
        <div><dt>Policy blocked</dt><dd>{metrics.policy_attack_blocked}/{metrics.policy_attack_total}</dd></div>
      </dl>
    </article>
  );
}

export function EvalModal({ legacyReport, holdout, active, versions, running, lifecycleRunning, error, onRunLegacy, onEvolve, onRollback, onClose }: {
  legacyReport: EvalReport | null;
  holdout: HoldoutReport | null;
  active: SkillVersion | null;
  versions: SkillVersion[];
  running: boolean;
  lifecycleRunning: boolean;
  error: string | null;
  onRunLegacy: () => Promise<void>;
  onEvolve: () => Promise<void>;
  onRollback: () => Promise<void>;
  onClose: () => void;
}) {
  const busy = running || lifecycleRunning;
  const canRollback = versions.some((item) => item.status === "retired");
  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="eval-modal" role="dialog" aria-modal="true">
        <div className="modal-heading">
          <div><span className="section-kicker">FROZEN HOLDOUT</span><h2>Skill lifecycle control plane</h2><p>Generate from reviewed training trajectories, then promote only after unseen safety and quality gates pass.</p></div>
          <button className="icon-button" onClick={onClose}><X size={19} /></button>
        </div>
        <div className="skill-toolbar">
          <div><span>ACTIVE VERSION</span><strong>{active?.version ?? "loading…"}</strong><code>{active?.digest.slice(0, 12)}</code></div>
          <button className="secondary-button" disabled={busy || !canRollback} onClick={onRollback}><RotateCcw size={15} />Rollback</button>
          <button className="primary-button" disabled={busy} onClick={onEvolve}>{lifecycleRunning ? <span className="spinner" /> : <Sparkles size={15} />}Generate + gate candidate</button>
        </div>
        {busy && <div className="eval-running"><span className="spinner" /><strong>{lifecycleRunning ? "Running the frozen holdout three times…" : "Running 36 deterministic trajectories…"}</strong></div>}
        {error && <div className="failure-banner"><strong>Evaluation failed</strong><p>{error}</p></div>}
        {holdout && !busy && <>
          <div className="eval-results holdout-results">
            <HoldoutCard title={`Baseline · ${holdout.baseline_version}`} metrics={holdout.baseline} />
            <HoldoutCard title={`Candidate · ${holdout.candidate_version}`} metrics={holdout.candidate} candidate />
          </div>
          <div className={`promotion-gate ${holdout.promotion_passed ? "" : "rejected"}`}><ShieldCheck size={20} /><div><span>PROMOTION GATE · {holdout.repeated_runs} REPEATS · LEAKAGE MAX {Math.round(holdout.max_train_holdout_similarity * 100)}%</span><strong>{holdout.promotion_passed ? "All gates passed — candidate promoted" : "Candidate rejected"}</strong><p>{holdout.gate_failures.length ? holdout.gate_failures.join(" · ") : "Known ≥85%, gain ≥20 points, OOD/injection ≥75%, 100% policy blocks, leakage audit and deterministic repeats."}</p></div></div>
        </>}
        {legacyReport && !busy && <>
          <div className="eval-section-title"><span>THREE-CONDITION COVERAGE</span><button className="text-button" onClick={onRunLegacy}>Run again</button></div>
          <div className="eval-results">
            {legacyReport.results.map((result) => <article className={result.condition === "evolved" ? "promoted" : ""} key={result.condition}>
              <div><span>{conditionLabels[result.condition]}</span></div><strong>{pct(result.diagnosis_accuracy)}</strong>
              <div className="eval-bar"><i style={{ width: pct(result.diagnosis_accuracy) }} /></div>
              <dl><div><dt>Correct</dt><dd>{result.correct}/{result.total}</dd></div><div><dt>Action</dt><dd>{pct(result.action_accuracy)}</dd></div><div><dt>Trace</dt><dd>{pct(result.trace_coverage)}</dd></div><div><dt>Unsafe</dt><dd>{pct(result.unsafe_action_rate)}</dd></div></dl>
            </article>)}
          </div>
        </>}
        {!holdout && !legacyReport && !busy && <div className="eval-empty"><FlaskConical size={30} /><strong>No evaluation report yet</strong><p>Generate a candidate or run the three-condition coverage benchmark.</p></div>}
        <div className="modal-footer"><div><BarChart3 size={16} /><span>Snapshots, evaluations, promotions and rollbacks are persisted with actor and reason.</span></div><button className="secondary-button" disabled={busy} onClick={onRunLegacy}><FlaskConical size={16} />Run coverage eval</button></div>
      </div>
    </div>
  );
}
