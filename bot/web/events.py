"""Tiny in-process pub/sub for live dashboard updates.

The activity cog publishes events here; WebSocket handlers subscribe. Import-safe
without Quart so the bot can publish whether or not the web server is running.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

_subscribers = set()


def subscribe():
    queue = asyncio.Queue(maxsize=200)
    _subscribers.add(queue)
    return queue


def unsubscribe(queue):
    _subscribers.discard(queue)


def publish(event):
    """Fan an event dict out to all subscribers. Drops events for slow clients."""
    for queue in list(_subscribers):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass
