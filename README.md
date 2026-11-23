# KubeHeal

*Self-healing Kubernetes, from chaos to verified recovery.*

KubeHeal injects a controlled failure, diagnoses it, proposes one bounded remediation, waits for a
human to approve it, executes it, and then **checks whether the workload actually came back** — and
records the MTTR so the next incident is cheaper than the last.

The point of the project is the part most demos skip: recovery is verified against the workload
(ready replicas, ready pods), not against the agent's own bookkeeping. A run whose workload never
recovers ends `failed` with no MTTR, and that path is tested.

## What it does

```
dashboard ──/api/*──► graph :8080 · faultlib :8081 · chaos :8082 · sim :8083 · agent :8084
                          │            │             │           │            │
                     K8s+Hubble    catalogue     Chaos Mesh   fault rules   LLM + chaos
                                                     or sim                  + graph + lib
```

- **One scenario API** over two backends: Chaos Mesh CRDs on a real cluster, or the faultable
  infrastructure simulator when there is none.
- **Evidence-based diagnosis.** The model receives the blast radius, the catalogue entry, the fault
  and recent incident memory. It returns a causal chain, an action, a target and a risk.
- **Deterministic authorisation.** The risk the model proposes is a *proposal*. A table in
  `agent/src/policy.py` sets a floor per action (`stop_scenario` ≥ low, `restart_deployment` ≥ medium,
  `scale_deployment` ≥ high, anything unknown = high) and the effective risk is the higher of the two.
  A model that says `low` for a restart gets an approval gate anyway.
- **Bounded remediation.** Exactly three actions, one per run, and the write path is a Kubernetes API
  patch — no shell, no exec, no arbitrary resources.
- **Verified recovery.** Verification polls the Deployment's available/ready replicas and its pods. A
  fault may still be injected while the workload is already healthy; that is reported as recovery,
  because the workload is what matters.
- **Incident memory.** The 20 most recent incidents per fault type feed the next diagnosis, with
  success rate, confidence (aborted runs excluded) and mean MTTR.
- **A console.** `dashboard/` renders the incident list, the injection form, live topology, approval,
  the diagnosis with its provenance, and memory.

## Services

| Directory | Dev port | Container | Role |
|---|---:|---:|---|
| `graph/` | 8080 | 8080 | Kubernetes topology and reverse-BFS blast radius. |
| `faultlib/` | 8081 | 8080 | Fault catalogue, taxonomy classification, failure rankings. |
| `chaos/` | 8082 | 8080 | Scenario state machine over Chaos Mesh or the simulator. |
| `sim/` | 8083 | 8080 | Faultable infrastructure simulator: volumes, subnets, instances, fault rules. |
| `agent/` | 8084 | 8080 | Detect → diagnose → heal_plan → approve → execute → verify → report. |
| `journeys/` | 8004 | 8080 | Deterministic, cluster-free resilience model. |

Each service is standalone: `src/main.py`, `src/config.py`, `src/events.py`, `src/models.py`,
`tests/`, `requirements.txt`, `Dockerfile`, `k8s/`, and a committed `openapi.json`. There is no shared
package — duplicated conventions and explicit HTTP contracts instead.

## Run it on minikube

Prerequisites: Docker, `minikube`, `kubectl`, `helm`, and `docker` permission for your user.

```bash
# six services, the console, and the workload the demo faults
./scripts/minikube-up.sh

# Chaos Mesh, so a scenario can kill a real pod
./scripts/chaos-mesh-up.sh

# the console (a port-forward is the only supported exposure)
kubectl -n kubeheal-system port-forward svc/kubeheal-dashboard 3000:80
open http://localhost:3000
```

`scripts/deploy.sh` does the same for a real cluster, with the node addresses, SSH user and key passed
as flags — no defaults are baked in.

### End-to-end, without the console

```bash
./scripts/e2e-minikube.sh
```

It injects a `pod_kill` against `demo-target`, starts an agent run, approves it, waits for
verification, and prints the action result, the verification, the MTTR and the memory row. It fails
non-zero unless the run completes **and** the workload was observed healthy.

## Run one service from source

```bash
cd sim
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src python -m uvicorn main:app --port 8083 --reload
curl localhost:8083/health
# {"status":"ok","service":"sim","version":"0.1.0"}
```

## Test

```bash
for s in sim chaos graph faultlib agent journeys; do (cd "$s" && python -m pytest -q); done
cd dashboard && npm ci && npm run build
```

Unit tests need no cluster, no Chaos Mesh and no API key.

## Diagnosis providers

`LLM_MODE` selects how the diagnosis is produced:

| mode | what it does |
|---|---|
| `stub` (default) | deterministic, no network — the shape is the contract, not the judgement |
| `cassette` | replays a recorded diagnosis keyed by the SHA-256 of the evidence payload; a miss raises rather than falling back |
| `openai` | a live call; needs `OPENAI_API_KEY` and the `openai` package |

Every run records `diagnosis_provenance` (`mode`, `model`, `prompt_sha256`), so a report can never
claim a judgement it did not make. The cassette shipped in `agent/fixtures/` is **authored**, and says
so (`captured_from: authored_fixture`); no live model output is included in this repository.

## What is synthetic

- `sim` is a fake cloud. It is a real state machine, but it is not infrastructure.
- The demo topology is a fixture tagged `topology_sources: ["synthetic_fixture"]` and every node
  carries `demo-data=synthetic`. On a cluster with RBAC, `graph` reads the real API instead and reports
  `["kubernetes"]`.
- `journeys` is a deterministic model of a customer journey. Every response says so; it is never
  measured traffic.

## Security

This is a lab system. There is no authentication, CORS is open, and it is meant to run behind a
port-forward on a cluster you own. The write path is one approved action per run, and the service
accounts are limited to the three fault CRDs, deployment patches, and read-only topology. Do not expose
it publicly.

## Licence

Apache-2.0. See `LICENSE` and `NOTICE`.
