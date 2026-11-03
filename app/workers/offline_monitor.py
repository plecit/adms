"""Worker monitoring status online/offline mesin absensi."""
import asyncio
import logging
from datetime import timedelta
from sqlalchemy import select

from app.config import settings
from app.db.session import get_db_session
from app.models.models import Device, utcnow

logger = logging.getLogger(__name__)


async def check_devices_online_status() -> None:
    """Memeriksa status online/offline setiap mesin berdasarkan last_seen_at."""
    now = utcnow()
    offline_threshold = timedelta(seconds=settings.ICLOCK_DELAY * 3)

    async with get_db_session() as db:
        stmt = select(Device).where(Device.is_active == True)
        devices = (await db.execute(stmt)).scalars().all()

        for dev in devices:
            if not dev.last_seen_at:
                continue

            time_diff = now - dev.last_seen_at
            if time_diff > offline_threshold:
                logger.warning(
                    f"Mesin {dev.name} ({dev.serial_number}) OFFLINE! "
                    f"Terakhir terlihat: {dev.last_seen_at.strftime('%Y-%m-%d %H:%M:%S UTC')} "
                    f"({int(time_diff.total_seconds())} detik yang lalu)"
                )


async def offline_monitor_loop() -> None:
    """Loop berkala monitoring status mesin."""
    logger.info("Worker Offline Monitor dimulai...")
    while True:
        try:
            await check_devices_online_status()
        except Exception as e:
            logger.error(f"Error pada offline monitor: {e}", exc_info=True)
        await asyncio.sleep(settings.ICLOCK_DELAY)
