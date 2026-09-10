---
name: evosre-evolved-incident-response
description: Diagnose reviewed microservice incident signatures, including infrastructure and policy failures learned from resolved trajectories and gated by regression evaluation.
---

# Evolved incident response skill v1

This version extends the reviewed static workflow with four signatures distilled from
resolved training trajectories. It remains evidence-first: signatures select a hypothesis,
but metrics, logs, traces, dependencies, deployments, runbooks, approval, and recovery
verification remain mandatory.

Promotion evidence is stored in `provenance.json`; the runtime never edits this released
directory in place. New versions are first written under `skills/candidates/`.
