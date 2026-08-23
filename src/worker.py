from __future__ import annotations

import os
import time

from redis import Redis

from .control_plane import ControlPlaneStore, Lease
from .hook_server import _run_pipeline
from .logger import setup_logging


def recover_queued_tasks(redis: Redis, store: ControlPlaneStore, stale_seconds: int) -> int:
    """Requeue tasks left behind by a crashed worker and restore the Redis queue."""
    recovered = store.requeue_stale(stale_seconds)
    for task_id in store.queued_task_ids():
        redis.rpush("auto-deploy:queue", task_id)
    return recovered


def run() -> None:
    logger = setup_logging(verbose=os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG")
    redis_url = os.getenv("PLATFORM_REDIS_URL", "").strip()
    if not redis_url:
        raise RuntimeError("PLATFORM_REDIS_URL is required for the deployment worker")
    redis = Redis.from_url(redis_url, decode_responses=True)
    store = ControlPlaneStore()
    recovered = recover_queued_tasks(
        redis, store, int(os.getenv("PLATFORM_TASK_STALE_SECONDS", "3600"))
    )
    if recovered:
        logger.warning("Requeued %d stale deployment task(s)", recovered)
    logger.info("Deployment worker started")
    while True:
        item = redis.blpop("auto-deploy:queue", timeout=30)
        if item is None:
            continue
        _, task_id = item
        task = store.get(task_id)
        if task is None or task["status"] != "queued":
            continue
        lease = Lease(f"deploy:{task['environment']}")
        if not lease.acquire():
            redis.rpush("auto-deploy:queue", task_id)
            time.sleep(1)
            continue
        store.update_task(task_id, status="running")
        _run_pipeline(
            lease,
            store,
            task_id,
            task["environment"],
            task["engine"],
            task["version"],
            task["commit"],
            logger,
        )


if __name__ == "__main__":
    run()
