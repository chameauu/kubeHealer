#!/usr/bin/env bash
# End-to-end run against the local minikube cluster.
#
# Injects a real pod-kill into the demo workload, lets the agent diagnose it,
# approves the proposed action as a human would, and prints what verification
# and incident memory recorded. Every number this prints comes from the
# cluster; nothing is simulated except the diagnosis provider's choice of
# action (LLM_MODE=stub in the deployment).
#
#   ./scripts/e2e-minikube.sh
#
# Exits non-zero if the run does not reach a terminal state, so it can gate a
# release check.

set -euo pipefail

NS="${KUBEHEAL_NAMESPACE:-kubeheal-system}"
TIMEOUT_APPROVAL="${TIMEOUT_APPROVAL:-60}"
TIMEOUT_RUN="${TIMEOUT_RUN:-180}"

CHAOS_PORT=18082
AGENT_PORT=18084
DASHBOARD_PORT=13000

PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do
    [[ -n "${pid:-}" ]] && kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT

say() { printf '\n=== %s\n' "$*"; }

port_forward() { # service local-port
  kubectl -n "$NS" port-forward "svc/$1" "$2:80" >/dev/null 2>&1 &
  PIDS+=("$!")
}

wait_for() { # url, label
  for _ in $(seq 1 60); do
    if curl -fsS "$1" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "timed out waiting for $2 at $1" >&2
  return 1
}

json() { python3 -c "import json,sys; print($1)"; }

say "pods before"
kubectl -n "$NS" get pods -l app=demo-target -o wide --no-headers

say "port-forwards"
port_forward kubeheal-chaos "$CHAOS_PORT"
port_forward kubeheal-agent "$AGENT_PORT"
port_forward kubeheal-dashboard "$DASHBOARD_PORT"
wait_for "http://127.0.0.1:${CHAOS_PORT}/health" "chaos"
wait_for "http://127.0.0.1:${AGENT_PORT}/health" "agent"
wait_for "http://127.0.0.1:${DASHBOARD_PORT}/healthz" "dashboard"

# 20 s, not 60: the fault must be over inside the agent's 60 s verify window,
# otherwise verification honestly reports "not recovered" and the run cannot end
# in `done` with an MTTR.
say "inject: pod_kill on app=demo-target, one pod, 20s"
SCENARIO=$(
  curl -fsS -X POST "http://127.0.0.1:${CHAOS_PORT}/scenarios" \
    -H 'content-type: application/json' \
    -d '{
          "name": "e2e-pod-kill",
          "domain": "chaos_mesh",
          "fault_type": "pod_kill",
          "target": {
            "namespace": "'"$NS"'",
            "label_selector": {"app": "demo-target"},
            "mode": "one"
          },
          "duration_seconds": 20
        }'
)
echo "$SCENARIO" | json 'json.dumps({k: v for k, v in json.loads(sys.stdin.read()).items() if k in ("id", "status", "fault_type", "backend_ref")})'
SCENARIO_ID=$(echo "$SCENARIO" | json 'json.load(sys.stdin)["id"]')

say "start the agent run"
curl -fsS -X POST "http://127.0.0.1:${AGENT_PORT}/runs" \
  -H 'content-type: application/json' \
  -d "{\"scenario\": $SCENARIO, \"scenario_id\": \"$SCENARIO_ID\"}" | json 'json.dumps(json.load(sys.stdin))'

say "waiting for the approval checkpoint"
STATUS=""
for _ in $(seq 1 "$TIMEOUT_APPROVAL"); do
  RUN=$(curl -fsS "http://127.0.0.1:${AGENT_PORT}/runs/${SCENARIO_ID}")
  STATUS=$(echo "$RUN" | json 'json.load(sys.stdin).get("status", "")')
  [[ "$STATUS" == "awaiting_approval" ]] && break
  sleep 1
done
if [[ "$STATUS" != "awaiting_approval" ]]; then
  echo "run did not reach the approval gate; status=${STATUS:-none}" >&2
  echo "$RUN" >&2
  exit 1
fi
echo "$RUN" | json 'json.dumps({
  "node": (d := json.load(sys.stdin)).get("node"),
  "status": d.get("status"),
  "action": (d.get("diagnosis") or {}).get("recommended_action"),
  "risk": (d.get("diagnosis") or {}).get("risk"),
})'

say "approve"
curl -fsS -X POST "http://127.0.0.1:${AGENT_PORT}/runs/${SCENARIO_ID}/approve" \
  | json '(lambda d: json.dumps({k: d.get(k) for k in ("status", "node", "approval_status")}))(json.load(sys.stdin))'

say "waiting for verification"
for _ in $(seq 1 "$TIMEOUT_RUN"); do
  RUN=$(curl -fsS "http://127.0.0.1:${AGENT_PORT}/runs/${SCENARIO_ID}")
  STATUS=$(echo "$RUN" | json 'json.load(sys.stdin).get("status", "")')
  case "$STATUS" in done | aborted | error) break ;; esac
  sleep 2
done
echo "$RUN" | json 'json.dumps({
  "status": (d := json.load(sys.stdin)).get("status"),
  "node": d.get("node"),
  "approval_status": d.get("approval_status"),
  "action_result": d.get("action_result"),
  "verify_result": d.get("verify_result"),
  "mttr_seconds": d.get("mttr_seconds"),
}, indent=2)'

say "pods after"
kubectl -n "$NS" get pods -l app=demo-target -o wide --no-headers

say "incident memory for pod_kill"
curl -fsS "http://127.0.0.1:${AGENT_PORT}/memory/pod_kill" | json 'json.dumps(json.load(sys.stdin), indent=2)'

say "scenario state"
curl -fsS "http://127.0.0.1:${CHAOS_PORT}/scenarios/${SCENARIO_ID}" \
  | json '(lambda d: json.dumps({k: d.get(k) for k in ("status", "ended_at", "live_status")}))(json.load(sys.stdin))'

say "result"
case "$STATUS" in
  done) echo "E2E ok: run ${SCENARIO_ID} reached ${STATUS} with mttr_seconds set" ;;
  *) echo "E2E failed: run ${SCENARIO_ID} ended ${STATUS}" >&2; exit 1 ;;
esac
