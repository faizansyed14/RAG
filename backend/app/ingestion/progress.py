"""
In-process pub/sub for ingestion progress, one queue per document. The
upload/status endpoints subscribe an SSE stream to a document's queue;
router.py and diagram_pipeline.py publish phase/page events into it as
ingestion actually happens -- this is what lets the frontend show real
chunking/OCR/indexing progress instead of a fake progress bar.

In-process only: fine for a single backend worker (this app's scope). A
multi-worker deployment would need a shared bus (Redis pub/sub) instead --
not needed here.
"""

import asyncio
import uuid

_queues: dict[uuid.UUID, list[asyncio.Queue]] = {}


def subscribe(document_id: uuid.UUID) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    _queues.setdefault(document_id, []).append(queue)
    return queue


def unsubscribe(document_id: uuid.UUID, queue: asyncio.Queue) -> None:
    subscribers = _queues.get(document_id)
    if subscribers and queue in subscribers:
        subscribers.remove(queue)
        if not subscribers:
            _queues.pop(document_id, None)


def publish(document_id: uuid.UUID, event: dict) -> None:
    for queue in _queues.get(document_id, []):
        queue.put_nowait(event)
