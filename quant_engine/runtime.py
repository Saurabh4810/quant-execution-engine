from __future__ import annotations
import asyncio, json, logging
from dataclasses import asdict

logger = logging.getLogger("quant_engine")

def log_event(event: str, **fields) -> None:
    logger.info(json.dumps({"event": event, **fields}, default=str, sort_keys=True))

async def consume(queue: asyncio.Queue, handler, stop: asyncio.Event) -> None:
    """Bounded queue gives upstream WebSocket code explicit back-pressure."""
    while not stop.is_set() or not queue.empty():
        try: item = await asyncio.wait_for(queue.get(), timeout=0.25)
        except asyncio.TimeoutError: continue
        try: await handler(item)
        finally: queue.task_done()
