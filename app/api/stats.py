"""REST API untuk ringkasan metrik dan statistik operasional ADMS."""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.models import Device, AttendanceLog, DeviceCommand, OutboxEvent, RawRequest, utcnow

router = APIRouter(prefix="/api/stats", tags=["System Statistics"])


@router.get("")
async def get_system_stats(db: AsyncSession = Depends(get_db)):
    """Mendapatkan statistik sistem secara real-time untuk dashboard."""
    now = utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    online_threshold = timedelta(seconds=120)

    # Perangkat
    devices = (await db.execute(select(Device))).scalars().all()
    total_devices = len(devices)
    online_devices = sum(
        1 for d in devices if d.last_seen_at and (now - d.last_seen_at) <= online_threshold
    )

    # Attendance logs hari ini
    today_punch_q = select(func.count()).select_from(AttendanceLog).where(
        AttendanceLog.received_at >= today_start
    )
    today_punches = (await db.execute(today_punch_q)).scalar() or 0

    # Total attendance logs sepanjang waktu
    total_punch_q = select(func.count()).select_from(AttendanceLog)
    total_punches = (await db.execute(total_punch_q)).scalar() or 0

    # Status Commands
    pending_cmd_q = select(func.count()).select_from(DeviceCommand).where(
        DeviceCommand.state == "pending"
    )
    pending_commands = (await db.execute(pending_cmd_q)).scalar() or 0

    dead_cmd_q = select(func.count()).select_from(DeviceCommand).where(
        DeviceCommand.state == "dead"
    )
    dead_commands = (await db.execute(dead_cmd_q)).scalar() or 0

    # Outbox Backlog
    pending_outbox_q = select(func.count()).select_from(OutboxEvent).where(
        OutboxEvent.state == "pending"
    )
    pending_outbox = (await db.execute(pending_outbox_q)).scalar() or 0

    # Raw requests hari ini
    today_raw_q = select(func.count()).select_from(RawRequest).where(
        RawRequest.received_at >= today_start
    )
    today_raw = (await db.execute(today_raw_q)).scalar() or 0

    return {
        "devices": {
            "total": total_devices,
            "online": online_devices,
            "offline": total_devices - online_devices,
        },
        "attendance": {
            "today_punches": today_punches,
            "total_punches": total_punches,
        },
        "commands": {
            "pending": pending_commands,
            "dead": dead_commands,
        },
        "outbox": {
            "pending": pending_outbox,
        },
        "raw_requests": {
            "today": today_raw,
        },
        "server_time_utc": now.isoformat(),
    }
