"""REST API untuk melihat log absensi dan audit konflik."""
from typing import List, Optional
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.models import AttendanceLog, AttendanceLogConflict

router = APIRouter(prefix="/api/attlog", tags=["Attendance Logs API"])


class AttendanceLogResponse(BaseModel):
    id: str
    device_sn: str
    pin: str
    punched_at: datetime
    punched_at_local: datetime
    status: int
    status_label: str
    verify_type: int
    verify_label: str
    workcode: Optional[str]
    received_at: datetime


STATUS_LABELS = {
    0: "Check-In",
    1: "Check-Out",
    2: "Break-Out",
    3: "Break-In",
    4: "OT-In",
    5: "OT-Out",
}

VERIFY_LABELS = {
    1: "Fingerprint",
    2: "Password",
    3: "RFID Card",
    15: "Face (Visible Light)",
}


@router.get("", response_model=List[AttendanceLogResponse])
async def list_attendance_logs(
    pin: Optional[str] = None,
    device_sn: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    debounce_seconds: Optional[int] = Query(None, description="Jendela filter debounce (detik)"),
    limit: int = Query(100, le=1000),
    db: AsyncSession = Depends(get_db),
):
    """Melihat log kehadiran terkini dengan opsi filter dan debounce."""
    query = select(AttendanceLog)
    conditions = []

    if pin:
        conditions.append(AttendanceLog.pin == pin)
    if device_sn:
        conditions.append(AttendanceLog.device_sn == device_sn)
    if start_date:
        conditions.append(AttendanceLog.punched_at >= start_date)
    if end_date:
        conditions.append(AttendanceLog.punched_at <= end_date)

    if conditions:
        query = query.where(and_(*conditions))

    query = query.order_by(desc(AttendanceLog.punched_at)).limit(limit)
    res = await db.execute(query)
    logs = res.scalars().all()

    result: List[AttendanceLogResponse] = []
    last_punch_per_user = {}

    for l in logs:
        # Terapkan debounce jika diminta (hanya untuk tampilan/laporan, tidak menghapus DB)
        if debounce_seconds and debounce_seconds > 0:
            last_time = last_punch_per_user.get(l.pin)
            if last_time and abs((last_time - l.punched_at).total_seconds()) < debounce_seconds:
                continue
            last_punch_per_user[l.pin] = l.punched_at

        result.append(
            AttendanceLogResponse(
                id=str(l.id),
                device_sn=l.device_sn,
                pin=l.pin,
                punched_at=l.punched_at,
                punched_at_local=l.punched_at_local,
                status=l.status,
                status_label=STATUS_LABELS.get(l.status, f"Status {l.status}"),
                verify_type=l.verify_type,
                verify_label=VERIFY_LABELS.get(l.verify_type, f"Type {l.verify_type}"),
                workcode=l.workcode,
                received_at=l.received_at,
            )
        )

    return result


@router.get("/conflicts")
async def list_conflicts(
    limit: int = Query(50, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Melihat catatan baris yang mengalami konflik (waktu sama namun atribut status/verify berbeda)."""
    stmt = select(AttendanceLogConflict).order_by(desc(AttendanceLogConflict.received_at)).limit(limit)
    res = await db.execute(stmt)
    conflicts = res.scalars().all()

    return [
        {
            "id": str(c.id),
            "existing_attlog_id": str(c.existing_attlog_id) if c.existing_attlog_id else None,
            "device_sn": c.device_sn,
            "pin": c.pin,
            "punched_at": c.punched_at,
            "conflict_data": c.conflict_data,
            "raw_line": c.raw_line,
            "received_at": c.received_at,
        }
        for c in conflicts
    ]
