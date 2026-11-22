# KubeHeal console

The operator console for KubeHeal. It drives the loop: inject a controlled fault, watch the agent
diagnose it, approve or reject the one proposed action, and read what previous incidents taught the
agent.

This is a lab tool. It runs behind a port-forward, has no authentication and no multi-tenancy, and
must never be exposed to the public internet.

## What is here

| View | State | What it does |
|---|---|---|
| **Incidents** | complete | Scenario list polled every 2 s, two-step fault injection, live blast-radius preview, stop/delete to the state rules |
| **Agent** | complete | Run list and pipeline, diagnosis and evidence, approve/reject, incident memory |
| **Overview** | placeholder | Explains the planned platform summary; calls no service |
| **Topology** | placeholder | Explains the planned graph view; draws no graph |
| **Chaos lab** | placeholder | Explains the planned simulator workbench; shows no inventory |
| **Fault library** | placeholder | Explains the planned catalogue page; lists nothing |

Placeholders never render sample data. Each one says what it will hold and that it is not connected
in this build. The two working pages show only what the services return, including their errors.

### Incidents

- The scenario list is polled every 2 seconds; the age is computed in the browser, while timestamps
  and statuses come from the payload.
- Injection is explicit about its two paths:
  - **safe** (`simulator`) writes a fault rule to the fake cloud and submits directly.
  - **live** (`chaos_mesh`) writes a Chaos Mesh experiment. The submit button stays disabled until a
    current blast-radius preview is on screen **and** the exact phrase `INJECT LIVE FAULT` is typed.
- The blast-radius preview calls `graph` with the namespace, fault type and sorted label selector,
  then lists affected nodes with hop distance and severity. A synthetic topology source is labelled
  as synthetic.
- `stop` is offered only while a scenario is `running`; `delete` only when it is `stopped`,
  `completed` or `failed`. If a click races the state machine, the service's `409` detail is shown
  unchanged and the list is refreshed.
- A selected scenario can be handed to the agent with **start diagnosis**, which posts the scenario
  to the agent and opens its run.

### Agent

- Runs are polled every 2 seconds and ordered as the agent returns them (most recently updated
  first). The selected run is polled separately so the pipeline stays current.
- The pipeline is `detect → diagnose → heal_plan → approve → execute → verify → report`. A run
  waiting at the approval interrupt highlights `approve`.
- The diagnosis panel shows the causal chain, proposed action, target, risk, provenance mode/model/
  prompt hash, and any parse error. The evidence panel shows the scenario snapshot, the run's blast
  radius, the catalogue entry and the memory context the agent was given.
- Approving or rejecting resumes the run. Verification can poll for up to 60 seconds, so the request
  may take that long; the buttons stay in a busy state. A `409` is reported as "this run is no longer
  waiting for approval" and refreshes both the run and the list. An optional reason is sent with the
  decision; the service records every manual decision as `operator`.
- Incident memory (`GET /memory/{fault_type}`) shows the payload's success rate, confidence and mean
  MTTR. MTTR is displayed from the payload and never recomputed in the browser.

## Run it

The console expects the services on their usual development ports:

| Service | Dev port | Path used by the console |
|---|---:|---|
| graph | 8080 | `/api/graph` |
| faultlib | 8081 | `/api/faultlib` |
| chaos | 8082 | `/api/chaos` |
| sim | 8083 | `/api/sim` |
| agent | 8084 | `/api/agent` |

```bash
npm install
npm run dev          # http://localhost:5174
```

Vite proxies each `/api/<service>` prefix to the matching port with the prefix stripped, so the same
paths work in the cluster behind nginx.

Start at least `chaos`, `faultlib`, `graph` and `agent` for the two working pages; `sim` is only
needed if you want the simulator to apply a fault.

### Build and typecheck

```bash
npx tsc --noEmit
npm run build        # tsc --noEmit && vite build, output in dist/
```

`npm ci && npm run build` also works from a clean checkout with the committed lockfile.

### Container

```bash
docker build -t kubeheal-dashboard:latest .
docker run --rm -p 3000:8080 kubeheal-dashboard:latest
```

The image expects the cluster DNS resolver, so outside a cluster add
`--add-host kube-dns.kube-system.svc.cluster.local:127.0.0.1` if you only want to check that
the app and `/healthz` are served; API calls still need the services.

The image is a multi-stage build: `node:22-alpine` compiles the app, `nginx:alpine` serves it as
uid 101 on port 8080. `nginx.conf` resolves the in-cluster service names at request time and strips
the `/api/<service>/` prefix before proxying. Unknown API prefixes and proxy failures return a JSON
`detail`, like the services do.

To deploy inside the cluster:

```bash
kubectl apply -f k8s/
kubectl -n kubeheal-system port-forward svc/kubeheal-dashboard 3000:80
# open http://localhost:3000
```

## Wire types and contract details

`src/api/types.ts` is derived from the committed OpenAPI schemas of `chaos`, `graph`, `faultlib` and
`sim`, plus the agent's run state, whose envelope is free-form in its schema. Three details are part
of the contract and easy to get wrong:

- `ScenarioStatus` is the full union `pending | running | stopping | stopped | completed | failed`.
  `stopping` is a real, non-terminal state and is shown as such.
- Timestamps use `ended_at`; the console reads that field and no other end timestamp.
- A scenario has no blast radius. Blast radius is a run field, computed at diagnosis time, and is
  only read from an agent run's payload here.

Every request goes through `src/api/client.ts`. A non-2xx response becomes an `ApiError` carrying
the HTTP status and the service's `detail` string, which the UI prints verbatim.

## Layout

```
dashboard/
├── src/
│   ├── api/          # one module per service, typed, sharing client.ts
│   ├── components/   # panels, tables, pills, pipeline, blast radius, run detail
│   ├── hooks/        # polling and a one-second clock
│   ├── lib/          # timestamp, duration and status helpers
│   ├── pages/        # the six views
│   ├── App.tsx       # shell and in-app view state
│   └── styles.css
├── nginx.conf
├── Dockerfile
└── k8s/
```

No router library: six views and no URLs to preserve, so the active view is component state. No
component library: the console is deliberately dense and terminal-adjacent, with system fonts and
monospace for ids, timestamps and payload text.

## Licence

Apache-2.0. See the repository `LICENSE` and `NOTICE`.
