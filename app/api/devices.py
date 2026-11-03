"""REST API untuk manajemen perangkat absensi ZKTeco."""
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.models import Device, utcnow
from app.services.command_queue import queue_command
from app.services.distribution import trigger_resync_all_from_master

router = APIRouter(prefix="/api/devices", tags=["Devices API"])


class DeviceResponse(BaseModel):
    id: str
    serial_number: str
    name: Optional[str]
    model: Optional[str]
    firmware: Optional[str]
    ip_last: Optional[str]
    tz_offset_minutes: int
    is_master: bool
    is_active: bool
    is_online: bool
    last_seen_at: Optional[datetime]
    capabilities: Dict[str, Any]


class DeviceUpdateRequest(BaseModel):
    name: Optional[str] = None
    is_master: Optional[bool] = None
    is_active: Optional[bool] = None
    tz_offset_minutes: Optional[int] = None


@router.get("", response_model=List[DeviceResponse])
async def list_devices(db: AsyncSession = Depends(get_db)):
    """Mendapatkan daftar seluruh mesin absensi dan status terkini."""
    stmt = select(Device).order_by(Device.created_at.asc())
    res = await db.execute(stmt)
    devices = res.scalars().all()

    now = utcnow()
    threshold = timedelta(seconds=120)

    result = []
    for d in devices:
        is_online = bool(d.last_seen_at and (now - d.last_seen_at) <= threshold)
        result.append(
            DeviceResponse(
                id=str(d.id),
                serial_number=d.serial_number,
                name=d.name or f"Device {d.serial_number}",
                model=d.model or "Unknown",
                firmware=d.firmware or "Unknown",
                ip_last=d.ip_last,
                tz_offset_minutes=d.tz_offset_minutes,
                is_master=d.is_master,
                is_active=d.is_active,
                is_online=is_online,
                last_seen_at=d.last_seen_at,
                capabilities=d.capabilities or {},
            )
        )
    return result


@router.patch("/{serial_number}")
async def update_device(
    serial_number: str,
    req: DeviceUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Memperbarui konfigurasi mesin (menandai master, mengubah nama, dll)."""
    stmt = select(Device).where(Device.serial_number == serial_number)
    res = await db.execute(stmt)
    dev = res.scalar_one_or_none()
    if not dev:
        raise HTTPException(status_code=404, detail="Mesin tidak ditemukan")

    if req.name is not None:
        dev.name = req.name
    if req.is_master is not None:
        dev.is_master = req.is_master
    if req.is_active is not None:
        dev.is_active = req.is_active
    if req.tz_offset_minutes is not None:
        dev.tz_offset_minutes = req.tz_offset_minutes

    await db.commit()
    return {"message": "Berhasil memperbarui konfigurasi mesin", "serial_number": serial_number}


@router.post("/{serial_number}/command")
async def send_device_command(
    serial_number: str,
    command_name: str = Query(..., description="INFO, CHECK, REBOOT, atau RESYNC"),
    db: AsyncSession = Depends(get_db),
):
    """Mengirim perintah kontrol ke mesin (CHECK, INFO, REBOOT, atau RESYNC)."""
    stmt = select(Device).where(Device.serial_number == serial_number)
    res = await db.execute(stmt)
    dev = res.scalar_one_or_none()
    if not dev:
        raise HTTPException(status_code=404, detail="Mesin tidak ditemukan")

    cmd_upper = command_name.upper()
    now_ts = int(utcnow().timestamp())

    if cmd_upper == "RESYNC":
        if not dev.is_master:
            raise HTTPException(status_code=400, detail="Perintah RESYNC hanya berlaku untuk mesin master")
        await trigger_resync_all_from_master(db, master_sn=serial_number)
        return {"message": "Perintah penarikan ulang data (DATA QUERY) berhasil diantrikan ke mesin master"}

    elif cmd_upper in ("INFO", "CHECK", "REBOOT"):
        cmd = await queue_command(
            db=db,
            device_sn=serial_number,
            command_type=cmd_upper,
            payload=cmd_upper,
            idempotency_key=f"ctrl:{cmd_upper}:{now_ts}",
            created_by="admin_api",
        )
        return {"message": f"Perintah {cmd_upper} berhasil diantrikan ke {serial_number}", "command_id": cmd.id}

    else:
        raise HTTPException(status_code=400, detail=f"Perintah tidak dikenal: {command_name}")
