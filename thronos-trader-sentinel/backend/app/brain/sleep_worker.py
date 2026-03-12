import asyncio
import logging
import os

from app.brain.predictor import PredictionEngine
from app.brain.sleep_trader import run_worker_loop


def _setup_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level)


async def _main() -> None:
    _setup_logging()
    engine = PredictionEngine()
    await run_worker_loop(engine, interval_s=int(os.getenv("SLEEP_WORKER_INTERVAL_S", "5")))


if __name__ == "__main__":
    asyncio.run(_main())
