"""Test untuk endpoint REST API dan Web Admin Dashboard."""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import Device, Employee, AttendanceLog, utcnow


@pytest.mark.asyncio
async def test_health_check_endpoint(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_api_devices_crud(client: AsyncClient, db_session: AsyncSession):
    # Buat device
    dev = Device(
        serial_number="TEST_DEV_01",
        name="Device Uji",
        model="SenseFace 4A",
        is_master=False,
        is_active=True,
    )
    db_session.add(dev)
    await db_session.commit()

    # GET /api/devices
    resp = await client.get("/api/devices")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["serial_number"] == "TEST_DEV_01"

    # PATCH /api/devices/TEST_DEV_01 (jadikan master)
    patch_resp = await client.patch("/api/devices/TEST_DEV_01", json={"is_master": True})
    assert patch_resp.status_code == 200

    # POST command CHECK
    cmd_resp = await client.post("/api/devices/TEST_DEV_01/command?command_name=CHECK")
    assert cmd_resp.status_code == 200
    assert "command_id" in cmd_resp.json()


@pytest.mark.asyncio
async def test_api_employees_crud(client: AsyncClient, db_session: AsyncSession):
    # Buat employee via API
    req = {
        "pin": "12345",
        "full_name": "Siti Nurhaliza",
        "card_no": "998877",
        "privilege": 0,
    }
    resp = await client.post("/api/employees", json=req)
    assert resp.status_code == 200
    assert resp.json()["pin"] == "12345"

    # List employees
    list_resp = await client.get("/api/employees")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1

    # Delete employee
    del_resp = await client.delete("/api/employees/12345?purge_from_devices=false")
    assert del_resp.status_code == 200


@pytest.mark.asyncio
async def test_api_stats(client: AsyncClient, db_session: AsyncSession):
    resp = await client.get("/api/stats")
    assert resp.status_code == 200
    stats = resp.json()
    assert "devices" in stats
    assert "attendance" in stats
    assert "commands" in stats


@pytest.mark.asyncio
async def test_web_ui_dashboard_pages(client: AsyncClient, db_session: AsyncSession):
    """Memastikan seluruh halaman Web UI dapat di-render tanpa template error."""
    # 1. Dashboard
    r_dash = await client.get("/")
    assert r_dash.status_code == 200
    assert "ADMS Server" in r_dash.text

    # 2. Devices
    r_dev = await client.get("/devices")
    assert r_dev.status_code == 200
    assert "Daftar Mesin Absensi" in r_dev.text

    # 3. Employees
    r_emp = await client.get("/employees")
    assert r_emp.status_code == 200
    assert "Daftar Karyawan" in r_emp.text

    # 4. Attlog
    r_att = await client.get("/attlog")
    assert r_att.status_code == 200
    assert "Log Kehadiran" in r_att.text

    # 5. Commands
    r_cmd = await client.get("/commands")
    assert r_cmd.status_code == 200
    assert "Antrian Perintah" in r_cmd.text

    # 6. Audit
    r_aud = await client.get("/audit")
    assert r_aud.status_code == 200
    assert "Raw Audit Log" in r_aud.text
