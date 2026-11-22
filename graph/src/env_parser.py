"""Extract Kubernetes Service references from container environment variables.

Only in-cluster Service DNS names count. A bare IP, an external host, an empty
value or a non-URL string yields nothing: inventing a dependency is worse than
missing one.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_CLUSTER_SUFFIXES: tuple[tuple[str, ...], ...] = ((), ("cluster", "local"))


def _service_ref(env_var: str, raw: str) -> dict[str, str] | None:
    if not raw:
        return None
    parsed = urlsplit(raw.strip())
    if parsed.scheme not in {"http", "https"}:
        return None
    labels = (parsed.hostname or "").split(".")
    # `<name>.<namespace>.svc`, optionally followed by `.cluster.local`.
    if len(labels) < 3 or labels[2] != "svc":
        return None
    name, namespace = labels[0], labels[1]
    if not name or not namespace:
        return None
    if tuple(labels[3:]) not in _CLUSTER_SUFFIXES:
        return None
    return {"name": name, "namespace": namespace, "env_var": env_var}


def parse_service_refs(env: dict[str, str]) -> list[dict[str, str]]:
    """Return one reference per environment variable that names a Service."""
    refs: list[dict[str, str]] = []
    for env_var in sorted(env):
        ref = _service_ref(env_var, env[env_var])
        if ref is not None:
            refs.append(ref)
    return refs
