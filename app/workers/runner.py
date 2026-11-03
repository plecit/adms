"""Runner utama untuk background worker ADMS."""
import asyncio
import logging
import signal
import sys

from app.config import settings
from app.workers.command_dispatcher import command_dispatcher_loop
from app.workers.outbox_forwarder import outbox_forwarder_loop
from app.workers.offline_monitor import offline_monitor_loop

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
)
logger = logging.getLogger("adms.worker")


async def main() -> None:
    logger.info("Memulai Background Workers ADMS...")
    tasks = [
        asyncio.create_task(command_dispatcher_loop()),
        asyncio.create_task(outbox_forwarder_loop()),
        asyncio.create_task(offline_monitor_loop()),
    ]

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Workers dihentikan (Cancelled).")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker dimatikan oleh pengguna.")
        sys.exit(0)
