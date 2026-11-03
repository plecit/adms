"""Worker untuk monitoring timeout command dan recovery retry (Command Dispatcher)."""
import asyncio
import logging
from datetime import timedelta
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db_session
from app.models.models import DeviceCommand, utcnow

logger = logging.getLogger(__name__)


async def run_command_timeout_checker() -> int:
    """Mengembalikan command yang berstatus 'sent' tetapi tidak mendapat ACK melewati batas waktu ke 'pending'."""
    now = utcnow()
    async with get_db_session() as db:
        # Cari command yang timeout
        stmt = (
            update(DeviceCommand)
            .where(
                DeviceCommand.state == "sent",
                DeviceCommand.next_attempt_at <= now,
            )
            .values(
                state="pending",
                last_error=f"Timeout: tidak ada ACK dalam {settings.COMMAND_TIMEOUT_SECONDS} detik",
            )
        )
        res = await db.execute(stmt)
        await db.commit()
        reclaimed = res.rowcount
        if reclaimed > 0:
            logger.warning(f"Mengembalikan {reclaimed} command yang timeout ke status 'pending' untuk retry")
        return reclaimed


async def command_dispatcher_loop() -> None:
    """Loop berkala untuk memeriksa timeout antrian command."""
    logger.info("Worker Command Dispatcher dimulai...")
    while True:
        try:
            await run_command_timeout_checker()
        except Exception as e:
            logger.error(f"Error pada loop command dispatcher: {e}", exc_info=True)
        await asyncio.sleep(15)
