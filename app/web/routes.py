"""Router web UI menggunakan Jinja2 Templates."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.models import (
    Device,
    Employee,
    AttendanceLog,
    AttendanceLogConflict,
    DeviceCommand,
    RawRequest,
    BiometricTemplate,
    utcnow,
)
from app.api.attlog import STATUS_LABELS, VERIFY_LABELS
from app.services.enrollment import create_or_update_employee

router = APIRouter(include_in_schema=False)

templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


@router.get("/", response_class=HTMLResponse)
async def web_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """Halaman utama dashboard ADMS."""
    now = utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    online_threshold = timedelta(seconds=120)

    devices = (await db.execute(select(Device).order_by(Device.created_at.asc()))).scalars().all()
    device_data = []
    for d in devices:
        is_online = bool(d.last_seen_at and (now - d.last_seen_at) <= online_threshold)
        device_data.append({
            "serial_number": d.serial_number,
            "name": d.name or f"Device {d.serial_number}",
            "model": d.model or "Unknown",
            "firmware": d.firmware,
            "ip_last": d.ip_last,
            "is_master": d.is_master,
            "is_online": is_online,
            "last_seen_at": d.last_seen_at,
        })

    # Total punches today
    today_q = select(func.count()).select_from(AttendanceLog).where(AttendanceLog.received_at >= today_start)
    today_punches = (await db.execute(today_q)).scalar() or 0

    # Pending commands
    pending_cmd_q = select(func.count()).select_from(DeviceCommand).where(DeviceCommand.state == "pending")
    pending_commands = (await db.execute(pending_cmd_q)).scalar() or 0

    # Dead commands
    dead_cmd_q = select(func.count()).select_from(DeviceCommand).where(DeviceCommand.state == "dead")
    dead_commands = (await db.execute(dead_cmd_q)).scalar() or 0

    # Latest logs
    latest_logs_raw = (
        await db.execute(
            select(AttendanceLog).order_by(desc(AttendanceLog.punched_at)).limit(10)
        )
    ).scalars().all()

    # Pre-fetch employee names
    emp_res = (await db.execute(select(Employee.pin, Employee.full_name))).all()
    emp_names = {pin: name for pin, name in emp_res}

    latest_logs = []
    for l in latest_logs_raw:
        latest_logs.append({
            "punched_at_local": l.punched_at_local,
            "device_sn": l.device_sn,
            "pin": l.pin,
            "status_label": STATUS_LABELS.get(l.status, f"Status {l.status}"),
            "verify_label": VERIFY_LABELS.get(l.verify_type, f"Type {l.verify_type}"),
        })

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "active_tab": "dashboard",
            "total_devices": len(devices),
            "online_devices": sum(1 for d in device_data if d["is_online"]),
            "today_punches": today_punches,
            "pending_commands": pending_commands,
            "dead_commands": dead_commands,
            "devices": device_data,
            "latest_logs": latest_logs,
            "emp_names": emp_names,
        },
    )


@router.get("/devices", response_class=HTMLResponse)
async def web_devices(request: Request, db: AsyncSession = Depends(get_db)):
    """Halaman kelola mesin absensi."""
    now = utcnow()
    threshold = timedelta(seconds=120)
    devices = (await db.execute(select(Device).order_by(Device.created_at.asc()))).scalars().all()
    device_data = [
        {
            "serial_number": d.serial_number,
            "name": d.name or f"Device {d.serial_number}",
            "model": d.model or "Unknown",
            "firmware": d.firmware,
            "tz_offset_minutes": d.tz_offset_minutes,
            "is_master": d.is_master,
            "is_online": bool(d.last_seen_at and (now - d.last_seen_at) <= threshold),
            "capabilities": d.capabilities or {},
        }
        for d in devices
    ]
    return templates.TemplateResponse(
        request=request,
        name="devices.html",
        context={"active_tab": "devices", "devices": device_data},
    )


@router.get("/employees", response_class=HTMLResponse)
async def web_employees(request: Request, db: AsyncSession = Depends(get_db)):
    """Halaman daftar dan tambah karyawan."""
    employees = (await db.execute(select(Employee).order_by(Employee.pin.asc()))).scalars().all()
    emp_list = []
    for emp in employees:
        fp_count = (await db.execute(
            select(func.count()).select_from(BiometricTemplate).where(
                BiometricTemplate.employee_id == emp.id, BiometricTemplate.kind == "finger"
            )
        )).scalar() or 0
        face_count = (await db.execute(
            select(func.count()).select_from(BiometricTemplate).where(
                BiometricTemplate.employee_id == emp.id, BiometricTemplate.kind == "face"
            )
        )).scalar() or 0
        emp_list.append({
            "pin": emp.pin,
            "full_name": emp.full_name,
            "card_no": emp.card_no,
            "privilege": emp.privilege,
            "is_active": emp.is_active,
            "needs_review": emp.needs_review,
            "finger_count": fp_count,
            "face_count": face_count,
        })
    return templates.TemplateResponse(
        request=request,
        name="employees.html",
        context={"active_tab": "employees", "employees": emp_list},
    )


@router.post("/employees/create")
async def web_create_employee(
    pin: str = Form(...),
    full_name: str = Form(...),
    card_no: Optional[str] = Form(None),
    privilege: int = Form(0),
    db: AsyncSession = Depends(get_db),
):
    """Handler form tambah karyawan."""
    await create_or_update_employee(
        db=db,
        pin=pin.strip(),
        full_name=full_name.strip(),
        card_no=card_no.strip() if card_no else None,
        privilege=privilege,
    )
    return RedirectResponse(url="/employees", status_code=303)


@router.post("/employees/delete")
async def web_delete_employee(
    pin: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Handler form hapus karyawan."""
    stmt = select(Employee).where(Employee.pin == pin)
    emp = (await db.execute(stmt)).scalar_one_or_none()
    if emp:
        emp.is_active = False
        # Queue delete command ke mesin
        devices = (await db.execute(select(Device).where(Device.is_active == True))).scalars().all()
        now_ts = int(utcnow().timestamp())
        for dev in devices:
            from app.services.command_queue import queue_command
            await queue_command(
                db=db,
                device_sn=dev.serial_number,
                command_type="DATA DELETE USERINFO",
                payload=f"DATA DELETE USERINFO PIN={pin}",
                idempotency_key=f"del_user:{pin}:{now_ts}",
                created_by="web_admin",
            )
        await db.commit()
    return RedirectResponse(url="/employees", status_code=303)


@router.get("/attlog", response_class=HTMLResponse)
async def web_attlog(
    request: Request,
    pin: Optional[str] = None,
    device_sn: Optional[str] = None,
    debounce: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Halaman data kehadiran real-time."""
    query = select(AttendanceLog)
    conds = []
    if pin:
        conds.append(AttendanceLog.pin == pin.strip())
    if device_sn:
        conds.append(AttendanceLog.device_sn == device_sn.strip())
    if conds:
        query = query.where(and_(*conds))

    query = query.order_by(desc(AttendanceLog.punched_at)).limit(200)
    raw_logs = (await db.execute(query)).scalars().all()

    filtered = []
    last_punch = {}
    for l in raw_logs:
        if debounce > 0:
            last_t = last_punch.get(l.pin)
            if last_t and abs((last_t - l.punched_at).total_seconds()) < debounce:
                continue
            last_punch[l.pin] = l.punched_at

        filtered.append({
            "punched_at": l.punched_at,
            "punched_at_local": l.punched_at_local,
            "device_sn": l.device_sn,
            "pin": l.pin,
            "status_label": STATUS_LABELS.get(l.status, f"Status {l.status}"),
            "verify_label": VERIFY_LABELS.get(l.verify_type, f"Type {l.verify_type}"),
            "workcode": l.workcode,
        })

    return templates.TemplateResponse(
        request=request,
        name="attlog.html",
        context={
            "active_tab": "attlog",
            "logs": filtered,
            "filter_pin": pin,
            "filter_sn": device_sn,
            "debounce": debounce,
        },
    )


@router.get("/commands", response_class=HTMLResponse)
async def web_commands(request: Request, db: AsyncSession = Depends(get_db)):
    """Halaman antrian command ke mesin."""
    cmds = (await db.execute(
        select(DeviceCommand).order_by(desc(DeviceCommand.id)).limit(100)
    )).scalars().all()
    return templates.TemplateResponse(
        request=request,
        name="commands.html",
        context={"active_tab": "commands", "commands": cmds},
    )


@router.get("/audit", response_class=HTMLResponse)
async def web_audit(request: Request, db: AsyncSession = Depends(get_db)):
    """Halaman raw audit log dan konflik."""
    conflicts = (await db.execute(
        select(AttendanceLogConflict).order_by(desc(AttendanceLogConflict.received_at)).limit(50)
    )).scalars().all()

    raw_reqs = (await db.execute(
        select(RawRequest).order_by(desc(RawRequest.id)).limit(50)
    )).scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="audit.html",
        context={
            "active_tab": "audit",
            "conflicts": conflicts,
            "raw_requests": raw_reqs,
        },
    )
