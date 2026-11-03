"""REST API untuk manajemen karyawan dan pendaftaran (Enrollment)."""
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.models import Employee, BiometricTemplate, Device, utcnow
from app.services.enrollment import create_or_update_employee
from app.services.command_queue import queue_command

router = APIRouter(prefix="/api/employees", tags=["Employees API"])


class EmployeeCreateRequest(BaseModel):
    pin: str = Field(..., description="PIN karyawan (1-18 digit angka)")
    full_name: str = Field(..., description="Nama lengkap karyawan")
    card_no: Optional[str] = None
    privilege: int = 0
    password: Optional[str] = None
    target_device_sns: Optional[List[str]] = None


class EmployeeResponse(BaseModel):
    id: str
    pin: str
    full_name: str
    card_no: Optional[str]
    privilege: int
    is_active: bool
    needs_review: bool
    finger_count: int
    face_count: int
    created_at: datetime
    updated_at: datetime


@router.get("", response_model=List[EmployeeResponse])
async def list_employees(
    needs_review: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
):
    """Mendapatkan daftar karyawan beserta jumlah template biometrik."""
    query = select(Employee).order_by(Employee.pin.asc())
    if needs_review is not None:
        query = query.where(Employee.needs_review == needs_review)

    res = await db.execute(query)
    employees = res.scalars().all()

    result = []
    for emp in employees:
        # Hitung jumlah sidik jari
        fp_q = select(func.count()).select_from(BiometricTemplate).where(
            BiometricTemplate.employee_id == emp.id,
            BiometricTemplate.kind == "finger",
        )
        fp_count = (await db.execute(fp_q)).scalar() or 0

        # Hitung jumlah wajah
        face_q = select(func.count()).select_from(BiometricTemplate).where(
            BiometricTemplate.employee_id == emp.id,
            BiometricTemplate.kind == "face",
        )
        face_count = (await db.execute(face_q)).scalar() or 0

        result.append(
            EmployeeResponse(
                id=str(emp.id),
                pin=emp.pin,
                full_name=emp.full_name,
                card_no=emp.card_no,
                privilege=emp.privilege,
                is_active=emp.is_active,
                needs_review=emp.needs_review,
                finger_count=fp_count,
                face_count=face_count,
                created_at=emp.created_at,
                updated_at=emp.updated_at,
            )
        )
    return result


@router.post("", response_model=EmployeeResponse)
async def create_employee(
    req: EmployeeCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Mendaftarkan karyawan baru dari web/API dan otomatis push ke mesin."""
    try:
        emp = await create_or_update_employee(
            db=db,
            pin=req.pin,
            full_name=req.full_name,
            privilege=req.privilege,
            card_no=req.card_no,
            password=req.password,
            target_device_sns=req.target_device_sns,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return EmployeeResponse(
        id=str(emp.id),
        pin=emp.pin,
        full_name=emp.full_name,
        card_no=emp.card_no,
        privilege=emp.privilege,
        is_active=emp.is_active,
        needs_review=emp.needs_review,
        finger_count=0,
        face_count=0,
        created_at=emp.created_at,
        updated_at=emp.updated_at,
    )


@router.delete("/{pin}")
async def delete_employee(
    pin: str,
    purge_from_devices: bool = Query(True, description="Kirim DATA DELETE USERINFO ke mesin"),
    db: AsyncSession = Depends(get_db),
):
    """Menghapus/menonaktifkan karyawan dan mengirim perintah DATA DELETE USERINFO ke mesin."""
    stmt = select(Employee).where(Employee.pin == pin)
    res = await db.execute(stmt)
    emp = res.scalar_one_or_none()
    if not emp:
        raise HTTPException(status_code=404, detail="Karyawan tidak ditemukan")

    emp.is_active = False

    if purge_from_devices:
        # Kirim DATA DELETE USERINFO ke semua mesin aktif
        dev_stmt = select(Device).where(Device.is_active == True)
        devices = (await db.execute(dev_stmt)).scalars().all()
        now_ts = int(utcnow().timestamp())

        for dev in devices:
            await queue_command(
                db=db,
                device_sn=dev.serial_number,
                command_type="DATA DELETE USERINFO",
                payload=f"DATA DELETE USERINFO PIN={pin}",
                idempotency_key=f"del_user:{pin}:{now_ts}",
                created_by="admin_delete",
            )

    await db.commit()
    return {"message": f"Karyawan PIN {pin} dinonaktifkan dan perintah hapus diantrikan ke mesin"}
