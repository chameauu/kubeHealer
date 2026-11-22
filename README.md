# KubeHeal

*Self-healing Kubernetes, from chaos to verified recovery.*

KubeHeal injects controlled failures into Kubernetes, diagnoses them with an LLM, remediates
behind a human approval gate, verifies that the system actually recovered, and records the MTTR
so the next incident is cheaper than the last.

Every service currently exposes `GET /health` and emits one JSON log line per event; the
service-specific endpoints described below are not implemented yet.

## Services

| Directory | Dev port | Container | Role |
|---|---:|---:|---|
| `sim/` | 8083 | 8080 | Faultable infrastructure simulator: volumes, subnets, instances, fault rules. |
| `chaos/` | 8082 | 8080 | Unified fault scenarios: Chaos Mesh CRDs or the simulator backend. |
| `graph/` | 8080 | 8080 | Kubernetes/Hubble topology and blast-radius computation. |
| `faultlib/` | 8081 | 8080 | Fault catalogue, taxonomy classification and failure rankings. |
| `agent/` | 8084 | 8080 | Detect, diagnose, plan, approve, execute, verify, report. |
| `journeys/` | 8004 | 8080 | Deterministic cluster-free resilience laboratory. |

`journeys` is the cluster-free laboratory: no Kubernetes, no Chaos Mesh, deterministic by seed.

## Layout

```
kubeHealer/
├── sim/          # fake infrastructure: volumes, subnets, instances, fault rules
├── chaos/        # unified scenario API (Chaos Mesh or simulator)
├── graph/        # topology + reverse-BFS blast radius
├── faultlib/     # 8-fault catalogue, 5 taxonomies, rankings
├── agent/        # detect → diagnose → heal_plan → approve → execute → verify → report
├── journeys/     # deterministic, cluster-free journeys
└── k8s/          # namespace shared by all services
```

Each service is standalone: `src/main.py`, `src/config.py`, `src/events.py`, `tests/`,
`requirements.txt`, `Dockerfile`, `k8s/`. There is no shared package — duplicated conventions
and explicit HTTP contracts instead.

## Run a service

```bash
cd sim
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src uvicorn main:app --port 8083 --reload
curl localhost:8083/health
# {"status":"ok","service":"sim","version":"0.1.0"}
```

## Test

```bash
for s in sim chaos graph faultlib agent journeys; do (cd "$s" && python -m pytest -q); done
```

## Conventions

- Python 3.11, FastAPI 0.115, Pydantic v2, structlog, SQLite in WAL mode where state is persisted.
- One event = one JSON line (`event_type`, `service`, `severity`, `component`, `timestamp`, `payload`).
- HTTP errors: `404` unknown, `409` conflicting state, `422` validation, `502` unreachable backend.
- Fail closed: an approval timeout or a failed verification is never reported as success.
- Synthetic data is labelled as synthetic — `sim` is a fake cloud, demo topology is
  `synthetic_fixture`, `journeys` is a model rather than traffic.

## Deploy

```bash
kubectl apply -f k8s/namespace.yaml
for s in sim chaos graph faultlib agent journeys; do
  kubectl apply -f "$s/k8s/"
done
```

Images are built locally as `kubeheal-<service>:latest` and imported into the cluster
(`imagePullPolicy: Never`). Containers run as uid 1000 and expose `8080`.

## Licence

Apache-2.0. See `LICENSE` and `NOTICE`.
