"""Worker pengiriman transactional outbox ke sistem hilir HR/Payroll."""
import asyncio
import logging
import random
from datetime import timedelta
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db_session
from app.models.models import OutboxEvent, utcnow

logger = logging.getLogger(__name__)


async def forward_outbox_batch() -> int:
    """Mengambil batch event pending dan mengirimkannya ke webhook sistem hilir."""
    if not settings.OUTBOX_ENABLED or not settings.OUTBOX_TARGET_URL:
        return 0

    now = utcnow()
    async with get_db_session() as db:
        dialect_name = db.bind.dialect.name if db.bind else "postgresql"
        stmt = (
            select(OutboxEvent)
            .where(
                OutboxEvent.state == "pending",
                OutboxEvent.next_attempt_at <= now,
            )
            .order_by(OutboxEvent.created_at.asc())
            .limit(settings.OUTBOX_BATCH_SIZE)
        )
        if "sqlite" not in dialect_name:
            stmt = stmt.with_for_update(skip_locked=True)

        res = await db.execute(stmt)
        events = res.scalars().all()

        if not events:
            return 0

        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"{settings.APP_NAME}-Outbox/{settings.APP_VERSION}",
        }
        if settings.OUTBOX_AUTH_HEADER:
            headers["Authorization"] = settings.OUTBOX_AUTH_HEADER

        async with httpx.AsyncClient(timeout=10.0) as client:
            for ev in events:
                ev.attempts += 1
                headers["X-Idempotency-Key"] = ev.idempotency_key
                payload = {
                    "event_id": str(ev.id),
                    "event_type": ev.event_type,
                    "aggregate_id": ev.aggregate_id,
                    "payload": ev.payload,
                    "created_at": ev.created_at.isoformat(),
                }

                try:
                    resp = await client.post(
                        settings.OUTBOX_TARGET_URL,
                        json=payload,
                        headers=headers,
                    )
                    if 200 <= resp.status_code < 300:
                        ev.state = "delivered"
                        ev.delivered_at = utcnow()
                        ev.last_error = None
                        logger.info(f"Outbox event {ev.id} ({ev.event_type}) sukses terkirim ke downstream")
                    else:
                        raise ValueError(f"HTTP {resp.status_code}: {resp.text[:200]}")

                except Exception as err:
                    err_msg = str(err)
                    ev.last_error = err_msg
                    if ev.attempts >= ev.max_attempts:
                        ev.state = "dead"
                        logger.error(f"Outbox event {ev.id} GAGAL PERMANEN (dead) setelah {ev.attempts} percobaan: {err_msg}")
                    else:
                        # Exponential backoff + jitter
                        jitter = random.uniform(1.0, 3.0)
                        backoff = min(600, (2 ** ev.attempts) * 2 + jitter)
                        ev.next_attempt_at = utcnow() + timedelta(seconds=backoff)
                        logger.warning(f"Outbox event {ev.id} gagal dikirim ({err_msg}). Retry dalam {backoff:.1f}s")

        await db.commit()
        return len(events)


async def outbox_forwarder_loop() -> None:
    """Loop berkala worker outbox."""
    logger.info("Worker Outbox Forwarder dimulai...")
    while True:
        try:
            processed = await forward_outbox_batch()
            if processed == 0:
                await asyncio.sleep(settings.OUTBOX_POLL_INTERVAL)
            else:
                # Jika ada item yang diproses, langsung proses batch berikutnya tanpa jeda panjang
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error pada loop outbox forwarder: {e}", exc_info=True)
            await asyncio.sleep(settings.OUTBOX_POLL_INTERVAL)
