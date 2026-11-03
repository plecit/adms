"""Layanan pendaftaran dan sinkronisasi karyawan (Enrollment Service)."""
import re
import uuid
import logging
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Employee, Device, EmployeeDeviceSync, utcnow
from app.services.command_queue import queue_command

logger = logging.getLogger(__name__)


def validate_pin(pin: str) -> bool:
    """Validasi PIN: wajib numerik dan panjang wajar (1-18 digit sesuai limit ZKTeco)."""
    return bool(pin and pin.isdigit() and 1 <= len(pin) <= 18)


def format_userinfo_command(
    pin: str,
    name: str,
    privilege: int = 0,
    card_no: Optional[str] = None,
    password: Optional[str] = None,
    grp: int = 1,
    tz: str = "0000000100000000",
) -> str:
    """Menyusun string payload perintah DATA UPDATE USERINFO.
    
    Kolom dipisahkan oleh tab (\\t).
    """
    card_str = card_no or ""
    pwd_str = password or ""
    # Filter karakter tab dan newline dari nama agar tidak merusak format protokol
    clean_name = re.sub(r"[\t\r\n]", " ", name).strip()
    return (
        f"DATA UPDATE USERINFO PIN={pin}\tName={clean_name}\tPri={privilege}\t"
        f"Passwd={pwd_str}\tCard={card_str}\tGrp={grp}\tTZ={tz}"
    )


async def create_or_update_employee(
    db: AsyncSession,
    pin: str,
    full_name: str,
    privilege: int = 0,
    card_no: Optional[str] = None,
    password: Optional[str] = None,
    target_device_sns: Optional[List[str]] = None,
    needs_review: bool = False,
) -> Employee:
    """Membuat atau memperbarui karyawan di DB dan mengirim command USERINFO ke mesin."""
    if not validate_pin(pin):
        raise ValueError(f"PIN tidak valid: '{pin}'. Wajib berupa angka 1-18 digit.")

    stmt = select(Employee).where(Employee.pin == pin)
    res = await db.execute(stmt)
    emp = res.scalar_one_or_none()

    now = utcnow()
    if emp:
        emp.full_name = full_name
        emp.privilege = privilege
        emp.card_no = card_no
        emp.updated_at = now
    else:
        emp = Employee(
            id=uuid.uuid4(),
            pin=pin,
            full_name=full_name,
            privilege=privilege,
            card_no=card_no,
            needs_review=needs_review,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(emp)

    await db.commit()
    await db.refresh(emp)

    # Sinkronisasi ke mesin tujuan
    await push_employee_to_devices(db, emp, target_device_sns)
    return emp


async def push_employee_to_devices(
    db: AsyncSession,
    employee: Employee,
    target_device_sns: Optional[List[str]] = None,
) -> None:
    """Mengantrikan perintah DATA UPDATE USERINFO ke mesin-mesin yang dituju."""
    query = select(Device).where(Device.is_active == True)
    if target_device_sns:
        query = query.where(Device.serial_number.in_(target_device_sns))

    devices = (await db.execute(query)).scalars().all()
    payload = format_userinfo_command(
        pin=employee.pin,
        name=employee.full_name,
        privilege=employee.privilege,
        card_no=employee.card_no,
    )

    for dev in devices:
        idempotency_key = f"user:{employee.pin}:v{int(employee.updated_at.timestamp())}"
        await queue_command(
            db=db,
            device_sn=dev.serial_number,
            command_type="DATA UPDATE USERINFO",
            payload=payload,
            idempotency_key=idempotency_key,
            created_by="enrollment_service",
        )

        # Catat atau update state di employee_device_sync
        sync_stmt = select(EmployeeDeviceSync).where(
            EmployeeDeviceSync.employee_id == employee.id,
            EmployeeDeviceSync.device_sn == dev.serial_number,
        )
        sync_record = (await db.execute(sync_stmt)).scalar_one_or_none()
        if not sync_record:
            sync_record = EmployeeDeviceSync(
                employee_id=employee.id,
                device_sn=dev.serial_number,
                state="pending",
            )
            db.add(sync_record)
        else:
            sync_record.state = "pending"

    await db.commit()
