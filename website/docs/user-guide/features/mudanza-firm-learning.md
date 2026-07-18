# Mudanza firm learning

Hermes can use Mudanza as its governed inference and learning control plane.
Mudanza supplies firm scope, model routing through Portkey, audit records,
evaluation, human approval, canaries, promotion, and rollback. Hermes supplies
the agent runtime and emits metadata-only lifecycle signals.

## Configure

Create a Hermes service token in Mudanza under **Settings → Firm Learning**.
The raw token is shown once. Store it in the Hermes profile environment:

```bash
MUDANZA_FIRM_LEARNING_TOKEN=mfl_...
MUDANZA_ROUTER_BASE_URL=https://tessara-prod-functions.azurewebsites.net/api/hermes/v1
MUDANZA_FIRM_LEARNING_ENABLED=true
```

Select provider `mudanza` and model `mudanza-auto`, then enable the
`mudanza_firm_learning` plugin. The provider sends every model call through the
firm-scoped Mudanza Model Router. The plugin observes completed turns, tools,
and approvals without sending prompt, response, argument, result, or transcript
content.

## Governance boundary

Observed signals do not change behavior. Mudanza aggregates repeated evidence
into candidates. A candidate must be evaluated and approved before a canary can
start, and must be explicitly promoted before it becomes durable firm memory.
Every promotion can be rolled back. Client and advisor memories remain scoped
and are never converted directly into firm knowledge.
