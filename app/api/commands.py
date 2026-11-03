"""REST API untuk monitoring dan pengelolaan antrian Device Commands."""
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.models import DeviceCommand, utcnow

router = APIRouter(prefix="/api/commands", tags=["Device Commands API"])


class CommandResponse(BaseModel):
    id: int
    device_sn: str
    command_type: str
    payload: str
    idempotency_key: str
    state: str
    attempts: int
    max_attempts: int
    next_attempt_at: datetime
    sent_at: Optional[datetime]
    acked_at: Optional[datetime]
    return_code: Optional[int]
    last_error: Optional[str]
    created_at: datetime


@router.get("", response_model=List[CommandResponse])
async def list_commands(
    device_sn: Optional[str] = None,
    state: Optional[str] = None,
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Melihat antrian perintah ke mesin beserta status eksekusinya."""
    query = select(DeviceCommand)
    if device_sn:
        query = query.where(DeviceCommand.device_sn == device_sn)
    if state:
        query = query.where(DeviceCommand.state == state)

    query = query.order_by(desc(DeviceCommand.id)).limit(limit)
    res = await db.execute(query)
    commands = res.scalars().all()

    return [
        CommandResponse(
            id=c.id,
            device_sn=c.device_sn,
            command_type=c.command_type,
            payload=c.payload,
            idempotency_key=c.idempotency_key,
            state=c.state,
            attempts=c.attempts,
            max_attempts=c.max_attempts,
            next_attempt_at=c.next_attempt_at,
            sent_at=c.sent_at,
            acked_at=c.acked_at,
            return_code=c.return_code,
            last_error=c.last_error,
            created_at=c.created_at,
        )
        for c in commands
    ]


@router.post("/{command_id}/retry")
async def retry_command(
    command_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Me-reset command yang failed atau dead agar dapat dicoba kirim ulang."""
    stmt = select(DeviceCommand).where(DeviceCommand.id == command_id)
    cmd = (await db.execute(stmt)).scalar_one_or_none()
    if not cmd:
        raise HTTPException(status_code=404, detail="Command tidak ditemukan")

    cmd.state = "pending"
    cmd.attempts = 0
    cmd.next_attempt_at = utcnow()
    cmd.last_error = "Manually retried by admin"

    await db.commit()
    return {"message": f"Command ID {command_id} berhasil di-reset ke status pending"}


@router.post("/{command_id}/cancel")
async def cancel_command(
    command_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Membatalkan command yang masih pending."""
    stmt = select(DeviceCommand).where(DeviceCommand.id == command_id)
    cmd = (await db.execute(stmt)).scalar_one_or_none()
    if not cmd:
        raise HTTPException(status_code=404, detail="Command tidak ditemukan")

    cmd.state = "cancelled"
    cmd.last_error = "Cancelled by admin"

    await db.commit()
    return {"message": f"Command ID {command_id} berhasil dibatalkan"}
