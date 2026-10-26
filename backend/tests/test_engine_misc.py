"""Rate limiter, budget and event hub tests."""

import asyncio

import pytest

from app.engine.budget import (
    ESTIMATED_CELL_COST_USD,
    ESTIMATED_IDENTITY_COST_USD,
    Budget,
    estimate_run_cost,
)
from app.engine.events import EventHub, HubRegistry
from app.engine.limiter import RateLimiter


async def test_limiter_burst_then_steady_rate() -> None:
    virtual_now = [0.0]

    async def fake_sleep(seconds: float) -> None:
        virtual_now[0] += seconds

    limiter = RateLimiter(5.0, monotonic=lambda: virtual_now[0], sleep=fake_sleep)
    for _ in range(11):
        await limiter.acquire()
    # 5 free tokens from the full bucket, then 6 waits of 0.2 s each.
    assert virtual_now[0] == pytest.approx(1.2)


async def test_limiter_refills_over_time() -> None:
    virtual_now = [0.0]

    async def fake_sleep(seconds: float) -> None:
        virtual_now[0] += seconds

    limiter = RateLimiter(2.0, monotonic=lambda: virtual_now[0], sleep=fake_sleep)
    for _ in range(2):
        await limiter.acquire()
    virtual_now[0] += 1.0  # two tokens refill
    await limiter.acquire()
    assert virtual_now[0] == pytest.approx(1.0)


def test_budget_affords_and_records() -> None:
    budget = Budget(cap=0.02)
    assert budget.affords(0.015)
    budget.record(0.007)
    assert not budget.affords(0.015)  # 0.007 + 0.015 > 0.02
    budget.record(None)
    assert budget.spent == pytest.approx(0.007)


def test_budget_uncapped() -> None:
    budget = Budget(cap=None)
    assert budget.affords(1000.0)
    assert budget.remaining is None


def test_estimate_is_at_least_documented_per_unit_cost() -> None:
    estimate = estimate_run_cost(pending_identity_rows=1, cell_count=2)
    assert estimate == pytest.approx(ESTIMATED_IDENTITY_COST_USD + 2 * ESTIMATED_CELL_COST_USD)
    assert estimate >= 0.021  # 3 synthetic calls at 0.007


def test_event_hub_fans_out_and_drops_oldest() -> None:
    hub = EventHub(maxsize=2)
    queue_a = hub.subscribe()
    queue_b = hub.subscribe()
    hub.publish({"n": 1})
    hub.publish({"n": 2})
    hub.publish({"n": 3})  # each queue evicts its oldest
    assert hub.dropped == 2
    assert [queue_a.get_nowait()["n"], queue_a.get_nowait()["n"]] == [2, 3]
    assert [queue_b.get_nowait()["n"], queue_b.get_nowait()["n"]] == [2, 3]


def test_event_hub_unsubscribe_and_registry() -> None:
    registry = HubRegistry(maxsize=4)
    hub = registry.for_sheet("s1")
    assert registry.for_sheet("s1") is hub
    queue = hub.subscribe()
    hub.unsubscribe(queue)
    hub.publish({"n": 1})
    assert queue.empty()
    registry.drop("s1")
    assert registry.for_sheet("s1") is not hub


async def test_event_hub_publish_never_blocks() -> None:
    hub = EventHub(maxsize=1)
    hub.subscribe()
    for i in range(10):
        hub.publish({"n": i})
    await asyncio.sleep(0)  # publish stayed synchronous
    assert hub.dropped == 9
