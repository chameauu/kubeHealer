"""Shared fixtures: every test starts empty and runs transitions fast."""

import pytest

from config import config
from faults import injector
from store import store


@pytest.fixture(autouse=True)
async def _isolated_fast_simulator():
    previous_speed = config.LIFECYCLE_SPEED
    config.LIFECYCLE_SPEED = 0.01
    await store.clear()
    injector.clear_all()
    yield
    await store.clear()
    injector.clear_all()
    config.LIFECYCLE_SPEED = previous_speed
