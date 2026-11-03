"""Test Integrasi Simulator Mesin Absensi (FakeDeviceClient) dengan Server ADMS."""
import pytest
from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient

from tools.fake_device import FakeDeviceClient, generate_sample_punches
from app.models.models import AttendanceLog


@pytest.mark.asyncio
async def test_fake_device_full_lifecycle(client: AsyncClient, db_session: AsyncSession):
    """Menguji siklus simulator: handshake, kirim punches, deteksi duplikat, dan polling."""
    # Patch FakeDeviceClient agar menggunakan ASGI client bawaan test
    device = FakeDeviceClient(
        server_url="http://testserver",
        serial_number="SF4A_SIM_99",
        model="SenseFace 4A",
    )

    # 1. Handshake
    hs_url = f"/iclock/cdata?SN={device.serial_number}&options=all&pushver=2.4.1"
    hs_resp = await client.get(hs_url)
    assert hs_resp.status_code == 200
    assert "GET OPTION FROM: SF4A_SIM_99" in hs_resp.text

    # 2. Kirim Sampel Absensi
    punches = generate_sample_punches(["10", "20"], count_per_pin=2)
    payload = device.build_attlog_payload(punches)

    att_url = f"/iclock/cdata?SN={device.serial_number}&table=ATTLOG&Stamp=1"
    att_resp = await client.post(
        att_url,
        content=payload.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
    )
    assert att_resp.status_code == 200
    assert att_resp.text.strip() == "OK:4"

    # Verifikasi 4 punch tersimpan di database
    q = select(func.count()).select_from(AttendanceLog).where(AttendanceLog.device_sn == device.serial_number)
    cnt = (await db_session.execute(q)).scalar()
    assert cnt == 4

    # 3. Kirim Ulang Payload Identik 5 Kali (Simulasi Jaringan Retry)
    for i in range(5):
        retry_resp = await client.post(
            f"/iclock/cdata?SN={device.serial_number}&table=ATTLOG&Stamp={i+2}",
            content=payload.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
        )
        assert retry_resp.status_code == 200
        assert retry_resp.text.strip() == "OK:4"

    # Jumlah di DB tetap 4
    cnt_after = (await db_session.execute(q)).scalar()
    assert cnt_after == 4

    # 4. Polling GetRequest (Idle Heartbeat)
    poll_resp = await client.get(f"/iclock/getrequest?SN={device.serial_number}")
    assert poll_resp.status_code == 200
    assert poll_resp.text.strip() == "OK"
