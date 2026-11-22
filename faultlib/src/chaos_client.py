"""HTTP client for the chaos engine's scenario listing."""

import httpx

import config


class ChaosClientError(RuntimeError):
    """The chaos engine could not be reached or rejected the request."""


class ChaosClient:
    """Thin async client reading the engine address from ``config.CHAOS_URL``."""

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url

    @property
    def base_url(self) -> str:
        return self._base_url or config.CHAOS_URL

    async def list_scenarios(self) -> list[dict]:
        """Return every recorded scenario, in the engine's order."""
        url = f"{self.base_url.rstrip('/')}/scenarios"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise ChaosClientError(str(exc)) from exc
        if response.status_code != 200:
            raise ChaosClientError(
                f"chaos rejected scenario listing ({response.status_code}): {response.text}"
            )
        payload = response.json()
        return list(payload["scenarios"])


chaos = ChaosClient()
