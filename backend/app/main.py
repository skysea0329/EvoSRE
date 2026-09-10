from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from secrets import token_hex

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import httpx

from .config import Settings, get_settings
from .database import Database
from .models import (
    ActionDecision,
    DashboardStats,
    EvalReport,
    HoldoutReport,
    IncidentCreate,
    IncidentEvent,
    IncidentRecord,
    IncidentStatus,
    LabState,
    ObservabilityMode,
    ObservabilityStatus,
    ScenarioSummary,
    SkillCondition,
    SkillEvolutionRequest,
    SkillEvolutionResult,
    SkillLifecycleEvent,
    SkillRollbackRequest,
    SkillVersionRecord,
    WorkflowControl,
)
from .services.evaluation import SkillEvaluator
from .services.fault_lab import FaultLab
from .services.runner import IncidentRunner
from .services.scenarios import list_scenarios, scenario_for
from .services.skill_registry import SkillRegistry
from .services.skill_lifecycle import SkillLifecycle


TERMINAL_STREAM_STATUSES = {
    IncidentStatus.AWAITING_APPROVAL,
    IncidentStatus.RESOLVED,
    IncidentStatus.DISMISSED,
    IncidentStatus.FAILED,
    IncidentStatus.PAUSED,
}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    database = Database(settings.database_path)
    database.initialize()
    lifecycle = SkillLifecycle(
        database,
        settings.database_path.parent / "skill-candidates",
        settings.database_path.parent / "latest-holdout.json",
    )
    lifecycle.initialize()
    runner = IncidentRunner(database, settings, SkillRegistry(database))
    fault_lab = FaultLab(database)
    evaluator = SkillEvaluator(settings.database_path.parent / "latest-eval.json")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await runner.start()
        yield
        await runner.stop()

    app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
    app.state.database = database
    app.state.runner = runner
    app.state.fault_lab = fault_lab
    app.state.evaluator = evaluator
    app.state.otel = runner.otel
    app.state.skill_lifecycle = lifecycle
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def require_incident(incident_id: str) -> IncidentRecord:
        incident = database.get_incident(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        return incident

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name, "environment": settings.app_env}

    @app.get("/api/scenarios", response_model=list[ScenarioSummary])
    async def scenarios() -> list[ScenarioSummary]:
        return list_scenarios()

    @app.get("/api/observability/status", response_model=ObservabilityStatus)
    async def observability_status() -> ObservabilityStatus:
        return await asyncio.to_thread(runner.otel.status)

    @app.get("/api/stats", response_model=DashboardStats)
    async def stats() -> DashboardStats:
        return database.stats()

    @app.get("/api/incidents", response_model=list[IncidentRecord])
    async def incidents() -> list[IncidentRecord]:
        return database.list_incidents()

    @app.post(
        "/api/incidents", response_model=IncidentRecord,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_incident(payload: IncidentCreate) -> IncidentRecord:
        live_scenarios = {"bad_deployment", "db_pool_exhaustion", "cache_outage", "payment_timeout"}
        if payload.observability_mode == ObservabilityMode.OTEL and payload.scenario.value not in live_scenarios:
            raise HTTPException(
                status_code=422,
                detail="The real OTel lab supports deployment, database pool, cache and payment faults.",
            )
        if payload.observability_mode == ObservabilityMode.OTEL:
            live_terminal = {IncidentStatus.RESOLVED, IncidentStatus.DISMISSED, IncidentStatus.FAILED}
            active_live = [
                item for item in database.list_incidents()
                if item.observability_mode == ObservabilityMode.OTEL and item.status not in live_terminal
            ]
            if active_live:
                raise HTTPException(
                    status_code=409,
                    detail=f"Live lab is owned by active incident {active_live[0].id}; resolve it first.",
                )
        definition = scenario_for(payload.scenario)
        now = datetime.now(UTC)
        incident = IncidentRecord(
            id=token_hex(6),
            title=definition.summary.name,
            service=definition.summary.service,
            scenario=payload.scenario,
            skill_condition=payload.skill_condition,
            observability_mode=payload.observability_mode,
            severity=definition.summary.severity,
            status=IncidentStatus.QUEUED,
            reporter=payload.reporter.strip(),
            created_at=now,
            updated_at=now,
            metadata={
                "fault_active": True,
                "alert_signal": definition.summary.signal,
                "scenario_description": definition.summary.description,
                "skill_condition": payload.skill_condition.value,
                "observability_mode": payload.observability_mode.value,
            },
        )
        if payload.observability_mode == ObservabilityMode.OTEL:
            try:
                otel_state = await asyncio.to_thread(
                    runner.otel_lab.inject, incident.id, payload.scenario.value
                )
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="Real OTel lab is unavailable. Start the Compose otel profile.",
                ) from exc
            incident = incident.model_copy(
                update={"metadata": {**incident.metadata, "otel_lab_state": otel_state}}
            )
        database.create_incident(incident)
        fault_lab.inject(incident)
        database.add_event(
            incident.id, "alert", "Fault injected",
            f"{definition.summary.description} Signal: {definition.summary.signal}.",
            {"scenario": payload.scenario.value, "reporter": incident.reporter,
             "skill_condition": payload.skill_condition.value,
             "observability_mode": payload.observability_mode.value},
        )
        await runner.enqueue("investigate", incident.id)
        return incident

    @app.post(
        "/api/incidents/demo", response_model=IncidentRecord,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def demo_incident() -> IncidentRecord:
        return await create_incident(
            IncidentCreate(
                scenario="bad_deployment",
                skill_condition=SkillCondition.EVOLVED,
                reporter="Demo on-call engineer",
            )
        )

    @app.get("/api/incidents/{incident_id}", response_model=IncidentRecord)
    async def incident_detail(incident_id: str) -> IncidentRecord:
        return require_incident(incident_id)

    @app.get("/api/incidents/{incident_id}/events", response_model=list[IncidentEvent])
    async def incident_events(incident_id: str) -> list[IncidentEvent]:
        require_incident(incident_id)
        return database.list_events(incident_id)

    @app.get("/api/incidents/{incident_id}/lab", response_model=LabState)
    async def incident_lab_state(incident_id: str) -> LabState:
        require_incident(incident_id)
        lab_state = database.get_lab_state(incident_id)
        if lab_state is None:
            raise HTTPException(status_code=404, detail="Fault lab state not found")
        return lab_state

    @app.get("/api/incidents/{incident_id}/events/stream")
    async def incident_event_stream(incident_id: str, request: Request) -> StreamingResponse:
        require_incident(incident_id)

        async def generate():
            last_id = 0
            idle_terminal_cycles = 0
            while not await request.is_disconnected():
                events = database.list_events(incident_id, after_id=last_id)
                for event in events:
                    last_id = event.id
                    yield f"id: {event.id}\ndata: {json.dumps(event.model_dump(mode='json'))}\n\n"
                incident = database.get_incident(incident_id)
                if incident and incident.status in TERMINAL_STREAM_STATUSES and not events:
                    idle_terminal_cycles += 1
                    if idle_terminal_cycles >= 2:
                        break
                else:
                    idle_terminal_cycles = 0
                await asyncio.sleep(0.2)

        return StreamingResponse(
            generate(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/incidents/{incident_id}/action/approve", response_model=IncidentRecord)
    async def approve_action(incident_id: str, payload: ActionDecision) -> IncidentRecord:
        require_incident(incident_id)
        try:
            updated, is_new = database.decide_action(incident_id, payload, approved=True)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if is_new:
            database.add_event(
                incident_id, "approval", "Remediation approved", payload.reason.strip(),
                {"actor": payload.actor.strip(), "proposal_id": updated.proposal.id,
                 "idempotency_key": payload.idempotency_key},
            )
            await runner.enqueue("remediate", incident_id)
        return updated

    @app.post("/api/incidents/{incident_id}/action/reject", response_model=IncidentRecord)
    async def reject_action(incident_id: str, payload: ActionDecision) -> IncidentRecord:
        require_incident(incident_id)
        try:
            updated, is_new = database.decide_action(incident_id, payload, approved=False)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if is_new:
            database.add_event(
                incident_id, "approval", "Remediation rejected", payload.reason.strip(),
                {"actor": payload.actor.strip(), "proposal_id": updated.proposal.id,
                 "idempotency_key": payload.idempotency_key},
            )
        return updated

    @app.post("/api/incidents/{incident_id}/pause", response_model=IncidentRecord)
    async def pause_workflow(incident_id: str, payload: WorkflowControl) -> IncidentRecord:
        require_incident(incident_id)
        try:
            updated = runner.pause(incident_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        database.add_event(
            incident_id, "control", "Pause requested by operator", payload.reason.strip(),
            {"actor": payload.actor.strip()},
        )
        return updated

    @app.post("/api/incidents/{incident_id}/resume", response_model=IncidentRecord)
    async def resume_workflow(incident_id: str, payload: WorkflowControl) -> IncidentRecord:
        require_incident(incident_id)
        try:
            updated = await runner.resume(incident_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        database.add_event(
            incident_id, "control", "Resume requested by operator", payload.reason.strip(),
            {"actor": payload.actor.strip()},
        )
        return updated

    @app.post("/api/evals/run", response_model=EvalReport)
    async def run_skill_eval() -> EvalReport:
        return await evaluator.run()

    @app.get("/api/evals/latest", response_model=EvalReport)
    async def latest_skill_eval() -> EvalReport:
        report = evaluator.latest()
        if report is None:
            raise HTTPException(status_code=404, detail="No evaluation has been run")
        return report

    @app.get("/api/skills/versions", response_model=list[SkillVersionRecord])
    async def skill_versions() -> list[SkillVersionRecord]:
        return database.list_skill_versions()

    @app.get("/api/skills/active", response_model=SkillVersionRecord)
    async def active_skill() -> SkillVersionRecord:
        active = database.get_active_skill()
        if active is None:
            raise HTTPException(status_code=404, detail="No active skill")
        return active

    @app.get("/api/skills/events", response_model=list[SkillLifecycleEvent])
    async def skill_events() -> list[SkillLifecycleEvent]:
        return database.list_skill_events()

    @app.post("/api/skills/evolve", response_model=SkillEvolutionResult)
    async def evolve_skill(payload: SkillEvolutionRequest) -> SkillEvolutionResult:
        return await asyncio.to_thread(lifecycle.evolve, payload.actor.strip(), payload.reason.strip())

    @app.post("/api/skills/rollback", response_model=SkillVersionRecord)
    async def rollback_skill(payload: SkillRollbackRequest) -> SkillVersionRecord:
        try:
            return await asyncio.to_thread(
                lifecycle.rollback,
                payload.actor.strip(),
                payload.reason.strip(),
                payload.target_version,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/evals/holdout/latest", response_model=HoldoutReport)
    async def latest_holdout_eval() -> HoldoutReport:
        if not lifecycle.report_path.exists():
            raise HTTPException(status_code=404, detail="No holdout evaluation has been run")
        return HoldoutReport.model_validate_json(
            lifecycle.report_path.read_text(encoding="utf-8")
        )

    return app


app = create_app()
