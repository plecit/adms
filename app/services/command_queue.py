"""Layanan antrian perintah ke mesin absensi (Device Command Queue)."""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.models import DeviceCommand, Device, utcnow

logger = logging.getLogger(__name__)


async def queue_command(
    db: AsyncSession,
    device_sn: str,
    command_type: str,
    payload: str,
    idempotency_key: str,
    created_by: str = "system",
    max_attempts: int = 5,
) -> DeviceCommand:
    """Memasukkan command ke antrian dengan kunci idempotensi.
    
    Jika sudah ada command dengan idempotency_key yang sama:
    - Jika masih 'pending', command lama diabaikan / tidak diduplikasi.
    - Jika 'failed' atau 'dead', dapat direset jika diperlukan.
    """
    # Cek command yang sudah ada dengan idempotency_key sama untuk device ini
    stmt = select(DeviceCommand).where(
        DeviceCommand.device_sn == device_sn,
        DeviceCommand.idempotency_key == idempotency_key,
    )
    res = await db.execute(stmt)
    existing = res.scalar_one_or_none()

    if existing:
        if existing.state in ("pending", "sent", "acked"):
            logger.info(f"Command {idempotency_key} untuk {device_sn} sudah ada (state={existing.state}). Melewati.")
            return existing
        elif existing.state in ("failed", "dead"):
            # Reset untuk retry
            existing.state = "pending"
            existing.attempts = 0
            existing.next_attempt_at = utcnow()
            existing.payload = payload
            await db.commit()
            return existing

    # Jika ada command lama bertipe sama untuk entitas yang sama (supersede versi lama)
    # Misal idempotency_key berformat 'user:101:v2' -> supersede 'user:101:*' yang masih pending
    prefix = ":".join(idempotency_key.split(":")[:-1]) if ":" in idempotency_key else None
    if prefix:
        supersede_stmt = (
            update(DeviceCommand)
            .where(
                DeviceCommand.device_sn == device_sn,
                DeviceCommand.idempotency_key.like(f"{prefix}:%"),
                DeviceCommand.state == "pending",
                DeviceCommand.idempotency_key != idempotency_key,
            )
            .values(state="cancelled", last_error="Superseded by newer version")
        )
        await db.execute(supersede_stmt)

    new_cmd = DeviceCommand(
        device_sn=device_sn,
        command_type=command_type,
        payload=payload,
        idempotency_key=idempotency_key,
        state="pending",
        attempts=0,
        max_attempts=max_attempts,
        next_attempt_at=utcnow(),
        created_by=created_by,
        created_at=utcnow(),
    )
    db.add(new_cmd)
    await db.commit()
    await db.refresh(new_cmd)
    logger.info(f"Command berhasil di-queue: ID={new_cmd.id} ke {device_sn} ({command_type})")
    return new_cmd


async def fetch_next_pending_command(
    db: AsyncSession,
    device_sn: str,
) -> Optional[DeviceCommand]:
    """Mengambil satu command berikutnya untuk dikirim ke mesin saat polling getrequest.
    
    Menggunakan FOR UPDATE SKIP LOCKED untuk mencegah race condition antar request paralel.
    """
    now = utcnow()
    dialect_name = db.bind.dialect.name if db.bind else "postgresql"

    stmt = (
        select(DeviceCommand)
        .where(
            DeviceCommand.device_sn == device_sn,
            DeviceCommand.state == "pending",
            DeviceCommand.next_attempt_at <= now,
        )
        .order_by(DeviceCommand.id.asc())
        .limit(1)
    )

    if "sqlite" not in dialect_name:
        stmt = stmt.with_for_update(skip_locked=True)

    res = await db.execute(stmt)
    cmd = res.scalar_one_or_none()

    if not cmd:
        return None

    # Tandai sebagai sent
    cmd.state = "sent"
    cmd.attempts += 1
    cmd.sent_at = now
    # Set timeout ekspektasi: jika tidak ada respon dalam COMMAND_TIMEOUT_SECONDS, worker akan retry
    cmd.next_attempt_at = now + timedelta(seconds=settings.COMMAND_TIMEOUT_SECONDS)

    await db.commit()
    await db.refresh(cmd)
    return cmd


async def process_device_command_ack(
    db: AsyncSession,
    device_sn: str,
    cmd_id: int,
    return_code: int,
    cmd_name: Optional[str] = None,
) -> bool:
    """Memproses balasan eksekusi command dari mesin via POST /iclock/devicecmd."""
    stmt = select(DeviceCommand).where(
        DeviceCommand.id == cmd_id,
        DeviceCommand.device_sn == device_sn,
    )
    res = await db.execute(stmt)
    cmd = res.scalar_one_or_none()

    if not cmd:
        logger.warning(f"DeviceCmd ACK diterima untuk command ID={cmd_id} yang tidak ditemukan pada SN={device_sn}")
        return False

    cmd.return_code = return_code
    now = utcnow()

    if return_code == 0:
        # Sukses
        cmd.state = "acked"
        cmd.acked_at = now
        cmd.last_error = None
        logger.info(f"Command ID={cmd_id} ({cmd.command_type}) BERHASIL dieksekusi oleh {device_sn}")
    else:
        # Gagal dieksekusi oleh mesin
        err_msg = f"Mesin melaporkan error: Return={return_code}"
        cmd.last_error = err_msg
        if cmd.attempts >= cmd.max_attempts:
            cmd.state = "dead"
            logger.error(f"Command ID={cmd_id} GAGAL PERMANEN (dead) pada {device_sn} setelah {cmd.attempts} percobaan. {err_msg}")
        else:
            # Jadwalkan retry dengan exponential backoff
            backoff_seconds = min(300, 10 * (2 ** (cmd.attempts - 1)))
            cmd.state = "pending"
            cmd.next_attempt_at = now + timedelta(seconds=backoff_seconds)
            logger.warning(f"Command ID={cmd_id} gagal pada {device_sn} (Return={return_code}). Retry ke-{cmd.attempts} dalam {backoff_seconds}s")

    await db.commit()
    return True
