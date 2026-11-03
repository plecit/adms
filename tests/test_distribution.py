"""Test Distribusi Biometrik dari Mesin Master ke Mesin Target."""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient

from app.models.models import Device, Employee, BiometricTemplate, DeviceCommand, EmployeeDeviceSync


@pytest.mark.asyncio
async def test_master_enrollment_fanout_to_compatible_target(client: AsyncClient, db_session: AsyncSession):
    """Enroll wajah di master -> otomatis fan-out command BIODATA ke SenseFace target, dan tidak kirim balik ke master."""
    # 1. Daftarkan Mesin Master (SenseFace Master)
    master = Device(
        serial_number="MASTER_SF4A",
        name="SenseFace 4A Master (Lobby)",
        model="SenseFace 4A",
        is_master=True,
        is_active=True,
        capabilities={"FaceCount": "1000", "pushver": "2.4.1"},
    )
    # 2. Daftarkan Mesin Target 1: SenseFace 4A (Kompatibel Face)
    target_sf4a = Device(
        serial_number="TARGET_SF4A",
        name="SenseFace 4A Target (Warehouse)",
        model="SenseFace 4A",
        is_master=False,
        is_active=True,
        capabilities={"FaceCount": "1000", "pushver": "2.4.1"},
    )
    # 3. Daftarkan Mesin Target 2: M2-LR (Hanya Fingerprint/RFID, tidak dukung Face)
    target_m2lr = Device(
        serial_number="TARGET_M2LR",
        name="M2-LR Target (Gate)",
        model="M2-LR",
        is_master=False,
        is_active=True,
        capabilities={"FingerCount": "2000", "pushver": "2.1.0"},
    )
    db_session.add_all([master, target_sf4a, target_m2lr])
    await db_session.commit()

    # 4. Mesin Master mengupload OPERLOG berisi pendaftaran wajah visible light (BIODATA Type 9)
    operlog_payload = (
        "USER PIN=777\tName=Dewi Sartika\tPri=0\tCard=554433\n"
        "BIODATA Pin=777\tNo=0\tIndex=0\tValid=1\tDuress=0\tType=9\tMajorVer=12\tMinorVer=0\tFormat=0\tTmp=dGVzdF9mYWNlX3RlbXBsYXRlXzEyMw==\n"
    )
    resp = await client.post(
        f"/iclock/cdata?SN=MASTER_SF4A&table=OPERLOG&Stamp=10",
        content=operlog_payload.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 200
    assert "OK:" in resp.text

    # 5. Verifikasi bahwa user otomatis dibuat di tabel employees
    emp_stmt = select(Employee).where(Employee.pin == "777")
    emp = (await db_session.execute(emp_stmt)).scalar_one_or_none()
    assert emp is not None
    assert emp.full_name == "Dewi Sartika"

    # 6. Verifikasi template tersimpan terenkripsi di biometric_templates
    tmpl_stmt = select(BiometricTemplate).where(BiometricTemplate.employee_id == emp.id)
    tmpl = (await db_session.execute(tmpl_stmt)).scalar_one_or_none()
    assert tmpl is not None
    assert tmpl.kind == "face"
    assert tmpl.slot == 0
    # Pastikan template tidak tersimpan plain text
    assert b"dGVzdF9mYWNlX3RlbXBsYXRlXzEyMw==" not in tmpl.template

    # 7. Verifikasi FAN-OUT:
    # A. Harus ADA command BIODATA untuk TARGET_SF4A
    sf_cmd_stmt = select(DeviceCommand).where(
        DeviceCommand.device_sn == "TARGET_SF4A",
        DeviceCommand.command_type == "DATA UPDATE BIODATA",
    )
    sf_cmd = (await db_session.execute(sf_cmd_stmt)).scalar_one_or_none()
    assert sf_cmd is not None
    assert "DATA UPDATE BIODATA Pin=777" in sf_cmd.payload

    # B. TIDAK BOLEH ada command fan-out kembali ke MASTER_SF4A (cegah loop)
    master_cmd_stmt = select(DeviceCommand).where(DeviceCommand.device_sn == "MASTER_SF4A")
    master_cmd = (await db_session.execute(master_cmd_stmt)).scalar_one_or_none()
    assert master_cmd is None

    # C. TIDAK BOLEH mengirim face template ke TARGET_M2LR (mesin tidak kompatibel)
    m2_cmd_stmt = select(DeviceCommand).where(
        DeviceCommand.device_sn == "TARGET_M2LR",
        DeviceCommand.command_type == "DATA UPDATE BIODATA",
    )
    m2_cmd = (await db_session.execute(m2_cmd_stmt)).scalar_one_or_none()
    assert m2_cmd is None

    # Status di employee_device_sync untuk M2LR harus 'incompatible'
    sync_stmt = select(EmployeeDeviceSync).where(
        EmployeeDeviceSync.employee_id == emp.id,
        EmployeeDeviceSync.device_sn == "TARGET_M2LR",
    )
    sync_rec = (await db_session.execute(sync_stmt)).scalar_one_or_none()
    assert sync_rec is not None
    assert sync_rec.state == "incompatible"
