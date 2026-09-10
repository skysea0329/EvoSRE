import type { DashboardStats, EvalReport, HoldoutReport, Incident, IncidentEvent, ObservabilityMode, ObservabilityStatus, Scenario, ScenarioKey, SkillCondition, SkillEvolutionResult, SkillVersion } from "./types";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail || "Request failed");
  }
  return response.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  stats: () => request<DashboardStats>("/api/stats"),
  scenarios: () => request<Scenario[]>("/api/scenarios"),
  incidents: () => request<Incident[]>("/api/incidents"),
  incident: (id: string) => request<Incident>(`/api/incidents/${id}`),
  events: (id: string) => request<IncidentEvent[]>(`/api/incidents/${id}/events`),
  inject: (scenario: ScenarioKey, reporter: string, skillCondition: SkillCondition, observabilityMode: ObservabilityMode) =>
    request<Incident>("/api/incidents", json({ scenario, reporter, skill_condition: skillCondition, observability_mode: observabilityMode })),
  demo: () => request<Incident>("/api/incidents/demo", { method: "POST" }),
  decide: (id: string, action: "approve" | "reject", actor: string, reason: string) =>
    request<Incident>(`/api/incidents/${id}/action/${action}`, json({
      actor, reason, idempotency_key: crypto.randomUUID(),
    })),
  pause: (id: string, actor: string, reason: string) =>
    request<Incident>(`/api/incidents/${id}/pause`, json({ actor, reason })),
  resume: (id: string, actor: string, reason: string) =>
    request<Incident>(`/api/incidents/${id}/resume`, json({ actor, reason })),
  runEval: () => request<EvalReport>("/api/evals/run", { method: "POST" }),
  observabilityStatus: () => request<ObservabilityStatus>("/api/observability/status"),
  activeSkill: () => request<SkillVersion>("/api/skills/active"),
  skillVersions: () => request<SkillVersion[]>("/api/skills/versions"),
  latestHoldout: () => request<HoldoutReport>("/api/evals/holdout/latest"),
  evolveSkill: (actor: string, reason: string) =>
    request<SkillEvolutionResult>("/api/skills/evolve", json({ actor, reason })),
  rollbackSkill: (actor: string, reason: string) =>
    request<SkillVersion>("/api/skills/rollback", json({ actor, reason })),
};
