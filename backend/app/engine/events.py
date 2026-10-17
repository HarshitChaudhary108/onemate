"""Per-sheet event hub with bounded subscriber queues (plan §4.3).

The runner publishes cell/row lifecycle events; the SSE layer (P5)
subscribes one queue per client. Each subscriber queue is bounded — the
hub NEVER blocks the runner on a slow client.

Drop policy (explicit): when a subscriber's queue is full, the OLDEST
queued event is evicted to make room for the new one. Merging is by
revision (last write wins), so a stale event is safe to lose and the
freshest state always survives. ``dropped`` counts evictions for
observability.
"""

import asyncio
from typing import Any

Event = dict[str, Any]

DEFAULT_QUEUE_SIZE = 256


class EventHub:
    """Fan-out of events to per-subscriber bounded queues."""

    def __init__(self, maxsize: int = DEFAULT_QUEUE_SIZE) -> None:
        self._maxsize = maxsize
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self.dropped = 0

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue[Event]:
        """Register a new subscriber queue (bounded)."""
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: Event) -> None:
        """Deliver to every subscriber without ever blocking.

        Full queues evict their oldest event first; the enqueue is still
        guarded so publish can never raise on a raced queue.
        """
        for queue in self._subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                else:
                    self.dropped += 1
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self.dropped += 1


class HubRegistry:
    """One lazily-created :class:`EventHub` per sheet id."""

    def __init__(self, maxsize: int = DEFAULT_QUEUE_SIZE) -> None:
        self._maxsize = maxsize
        self._hubs: dict[str, EventHub] = {}

    def for_sheet(self, sheet_id: str) -> EventHub:
        hub = self._hubs.get(sheet_id)
        if hub is None:
            hub = self._hubs[sheet_id] = EventHub(self._maxsize)
        return hub

    def drop(self, sheet_id: str) -> None:
        self._hubs.pop(sheet_id, None)
