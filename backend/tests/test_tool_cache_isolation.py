"""
Regression: surface_* tools must resolve indices against per-request fetch results, not a
process-global cache. With the old module-level globals, two users' requests on the same warm
serverless instance could interleave and one user's surface_tasks/surface_cards would read the
other user's fetched rows. ContextVars are copied per asyncio task, so each request is isolated.
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import context
from tools.memory import surface_tasks
from tools.google_services import surface_cards


async def _set_tasks_then_read(items, indices):
    context.current_task_fetch.set(items)
    await asyncio.sleep(0)  # force the two tasks to interleave between set and read
    res = await surface_tasks({"indices": indices})
    return [r["id"] for r in res["items_data"]]


def test_surface_tasks_isolated_across_concurrent_tasks():
    async def run():
        return await asyncio.gather(
            _set_tasks_then_read([{"id": "a1"}, {"id": "a2"}], [0, 1]),
            _set_tasks_then_read([{"id": "b1"}], [0]),
        )

    a_ids, b_ids = asyncio.run(run())
    assert a_ids == ["a1", "a2"]   # would be ["b1", ...] under the old global-cache bug
    assert b_ids == ["b1"]


def test_surface_tasks_ignores_out_of_range_indices():
    async def run():
        context.current_task_fetch.set([{"id": "x"}])
        return await surface_tasks({"indices": [0, 5, 99]})

    res = asyncio.run(run())
    assert [r["id"] for r in res["items_data"]] == ["x"]


async def _set_emails_then_read(items, indices):
    context.current_email_fetch.set(items)
    await asyncio.sleep(0)
    res = await surface_cards({"source": "updates", "indices": indices})
    return [r["sender"] for r in res["items_data"]]


def test_surface_cards_isolated_across_concurrent_tasks():
    async def run():
        return await asyncio.gather(
            _set_emails_then_read([{"sender": "alice@x.com"}], [0]),
            _set_emails_then_read([{"sender": "bob@y.com"}], [0]),
        )

    a, b = asyncio.run(run())
    assert a == ["alice@x.com"]
    assert b == ["bob@y.com"]
