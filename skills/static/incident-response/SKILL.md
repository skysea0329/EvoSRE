---
name: evosre-static-incident-response
description: Diagnose common microservice incidents from metrics, logs, traces, dependencies, and deployment evidence before proposing one bounded remediation.
---

# Static incident response skill

1. Query service metrics, correlated logs, and distributed traces before selecting a cause.
2. Separate local resource faults from dependency, deployment, and policy faults.
3. Cite immutable evidence IDs for the leading hypothesis.
4. Read the operator runbook before proposing a state-changing action.
5. Never execute remediation without an explicit operator approval.
6. Re-query fresh health signals after the action and resolve only when every required check passes.

The accompanying `signatures.json` contains the initial eight reviewed failure signatures.
