"""Layanan distribusi biometrik dari mesin master ke mesin target yang kompatibel."""
import uuid
import base64
import logging
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Device,
    Employee,
    BiometricTemplate,
    EmployeeDeviceSync,
    utcnow,
)
from app.parsers.operlog import parse_operlog_payload, ParsedOperlogBiometric, ParsedOperlogUser
from app.services.crypto import calculate_sha256, encrypt_biometric_template, decrypt_biometric_template
from app.services.command_queue import queue_command
from app.services.enrollment import format_userinfo_command

logger = logging.getLogger(__name__)


def check_biometric_compatibility(target_device: Device, kind: str, format_version: str) -> bool:
    """Memeriksa apakah mesin target kompatibel dengan jenis dan format template biometrik."""
    model_name = (target_device.model or target_device.name or "").lower()
    caps = target_device.capabilities or {}

    # SenseFace 4A spesialis Visible Light Face
    is_senseface = "senseface" in model_name or "sf4" in model_name or "face" in model_name
    # M2-LR terminal multi-biometrik / finger / RFID
    is_m2lr = "m2" in model_name or "m2-lr" in model_name

    if kind == "face":
        # Wajah visible light hanya dikirim ke SenseFace atau device yang memiliki kapabilitas Face
        has_face_cap = caps.get("FaceCount") is not None or caps.get("FaceFunOn") == "1"
        if is_m2lr and not has_face_cap:
            return False
        return is_senseface or has_face_cap or not is_m2lr

    elif kind == "finger":
        # Sidik jari dikirim ke mesin yang mendukung fingerprint (M2-LR atau SenseFace jika ada)
        has_fp_cap = caps.get("FingerCount") is not None or caps.get("FPFunOn") == "1"
        return is_m2lr or has_fp_cap or not is_senseface

    return True


async def handle_operlog_ingest(
    db: AsyncSession,
    device_sn: str,
    raw_request_id: int,
    payload_text: str,
    stamp: Optional[str] = None,
) -> int:
    """Mengolah payload tabel OPERLOG: update user & pendaftaran biometrik."""
    parse_res = parse_operlog_payload(payload_text)
    now = utcnow()

    # Dapatkan info mesin pengirim
    dev_stmt = select(Device).where(Device.serial_number == device_sn)
    dev_res = await db.execute(dev_stmt)
    source_device = dev_res.scalar_one_or_none()
    is_master = source_device.is_master if source_device else False

    # 1. Tangani USER baru/update dari mesin
    for u in parse_res.users:
        emp_stmt = select(Employee).where(Employee.pin == u.pin)
        emp = (await db.execute(emp_stmt)).scalar_one_or_none()

        if not emp:
            # User dibuat langsung di mesin fisik: catat dengan needs_review=True
            emp = Employee(
                id=uuid.uuid4(),
                pin=u.pin,
                full_name=u.name,
                privilege=u.privilege,
                card_no=u.card_no,
                needs_review=True,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            db.add(emp)
            await db.flush()
            logger.info(f"User baru terdeteksi dari mesin {device_sn}: PIN={u.pin}, Name={u.name} (needs_review=True)")
        else:
            # Update data jika ada perubahan kartu atau nama
            if u.card_no and emp.card_no != u.card_no:
                emp.card_no = u.card_no
            if u.privilege != emp.privilege:
                emp.privilege = u.privilege
            emp.updated_at = now

    # 2. Tangani template biometrik (FP / FACE / BIODATA)
    for bio in parse_res.biometrics:
        emp_stmt = select(Employee).where(Employee.pin == bio.pin)
        emp = (await db.execute(emp_stmt)).scalar_one_or_none()
        if not emp:
            # Jika user belum ada, buat entri otomatis
            emp = Employee(
                id=uuid.uuid4(),
                pin=bio.pin,
                full_name=f"User {bio.pin}",
                needs_review=True,
                created_at=now,
                updated_at=now,
            )
            db.add(emp)
            await db.flush()

        raw_bytes = bio.template_b64.encode("utf-8")
        sha = calculate_sha256(raw_bytes)

        # Cek apakah template ini sudah ada
        tmpl_stmt = select(BiometricTemplate).where(
            BiometricTemplate.employee_id == emp.id,
            BiometricTemplate.kind == bio.kind,
            BiometricTemplate.slot == bio.slot,
        )
        existing_tmpl = (await db.execute(tmpl_stmt)).scalar_one_or_none()

        if existing_tmpl:
            if existing_tmpl.template_sha256 == sha:
                # Template identik, tidak ada perubahan
                continue
            # Update template dengan versi baru
            existing_tmpl.template = encrypt_biometric_template(raw_bytes)
            existing_tmpl.template_sha256 = sha
            existing_tmpl.version += 1
            existing_tmpl.source_device_sn = device_sn
            existing_tmpl.format_version = bio.format_version
            current_version = existing_tmpl.version
        else:
            # Buat template baru terenkripsi
            encrypted = encrypt_biometric_template(raw_bytes)
            new_tmpl = BiometricTemplate(
                id=uuid.uuid4(),
                employee_id=emp.id,
                kind=bio.kind,
                slot=bio.slot,
                format_version=bio.format_version,
                template=encrypted,
                template_sha256=sha,
                source_device_sn=device_sn,
                version=1,
                created_at=now,
            )
            db.add(new_tmpl)
            current_version = 1

        # 3. Jika berasal dari mesin MASTER, lakukan FAN-OUT ke mesin target yang kompatibel
        if is_master:
            await fan_out_template_to_compatible_devices(
                db=db,
                employee=emp,
                bio=bio,
                version=current_version,
                source_sn=device_sn,
            )

    # Update stamp mesin
    if source_device and stamp:
        source_device.last_operlog_stamp = stamp
        source_device.last_seen_at = now

    await db.commit()
    total_consumed = len(parse_res.users) + len(parse_res.biometrics) + len(parse_res.others)
    return total_consumed


async def fan_out_template_to_compatible_devices(
    db: AsyncSession,
    employee: Employee,
    bio: ParsedOperlogBiometric,
    version: int,
    source_sn: str,
) -> None:
    """Mendistribusikan template biometrik ke semua mesin aktif lain yang kompatibel."""
    stmt = select(Device).where(
        Device.is_active == True,
        Device.serial_number != source_sn,  # Hindari kirim balik ke mesin asal (cegah loop)
    )
    targets = (await db.execute(stmt)).scalars().all()

    for dev in targets:
        # Periksa kompatibilitas
        is_compatible = check_biometric_compatibility(dev, bio.kind, bio.format_version)
        if not is_compatible:
            logger.info(
                f"Melewati distribusi template {bio.kind} PIN={employee.pin} ke {dev.serial_number}: "
                f"Mesin tidak kompatibel."
            )
            # Tandai status di sync table
            sync_stmt = select(EmployeeDeviceSync).where(
                EmployeeDeviceSync.employee_id == employee.id,
                EmployeeDeviceSync.device_sn == dev.serial_number,
            )
            sync_rec = (await db.execute(sync_stmt)).scalar_one_or_none()
            if not sync_rec:
                sync_rec = EmployeeDeviceSync(
                    employee_id=employee.id,
                    device_sn=dev.serial_number,
                    state="incompatible",
                )
                db.add(sync_rec)
            else:
                sync_rec.state = "incompatible"
            continue

        # Format perintah ke mesin
        if bio.kind == "face" or bio.type_code == 9:
            # Perintah BIODATA Type 9 untuk SenseFace 4A
            payload = (
                f"DATA UPDATE BIODATA Pin={bio.pin}\tNo={bio.slot}\tIndex={bio.slot}\t"
                f"Valid=1\tDuress=0\tType=9\tMajorVer=12\tMinorVer=0\tFormat=0\tTmp={bio.template_b64}"
            )
            cmd_type = "DATA UPDATE BIODATA"
        else:
            # Perintah FINGERTMP untuk fingerprint
            payload = (
                f"DATA UPDATE FINGERTMP PIN={bio.pin}\tFID={bio.slot}\t"
                f"Size={len(bio.template_b64)}\tValid=1\tTMP={bio.template_b64}"
            )
            cmd_type = "DATA UPDATE FINGERTMP"

        idempotency_key = f"biotmp:{bio.pin}:{bio.kind}:{bio.slot}:v{version}"
        await queue_command(
            db=db,
            device_sn=dev.serial_number,
            command_type=cmd_type,
            payload=payload,
            idempotency_key=idempotency_key,
            created_by=f"fanout_from_{source_sn}",
        )
        logger.info(f"Mendistribusikan template {bio.kind} PIN={bio.pin} dari {source_sn} ke {dev.serial_number}")


async def trigger_resync_all_from_master(db: AsyncSession, master_sn: str) -> None:
    """Mengirim perintah DATA QUERY ke mesin master untuk menarik ulang seluruh user dan biometrik."""
    # Query semua userinfo
    await queue_command(
        db=db,
        device_sn=master_sn,
        command_type="DATA QUERY USERINFO",
        payload="DATA QUERY USERINFO",
        idempotency_key=f"query:userinfo:{int(utcnow().timestamp())}",
        created_by="admin_resync",
    )
    # Query face biometrik
    await queue_command(
        db=db,
        device_sn=master_sn,
        command_type="DATA QUERY BIODATA",
        payload="DATA QUERY BIODATA Type=9",
        idempotency_key=f"query:biodata_9:{int(utcnow().timestamp())}",
        created_by="admin_resync",
    )
    # Query fingerprint
    await queue_command(
        db=db,
        device_sn=master_sn,
        command_type="DATA QUERY FINGERTMP",
        payload="DATA QUERY FINGERTMP",
        idempotency_key=f"query:fingertmp:{int(utcnow().timestamp())}",
        created_by="admin_resync",
    )
