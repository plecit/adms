"""Test Antrian Perintah ke Mesin (Device Commands Queue)."""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient

from app.models.models import Device, DeviceCommand
from app.services.command_queue import queue_command


@pytest.mark.asyncio
async def test_command_lifecycle_polling_and_ack(client: AsyncClient, db_session: AsyncSession):
    """Siklus lengkap: antrikan command -> diambil saat getrequest -> konfirmasi hasil di devicecmd."""
    sn = "DEV_CMD_01"

    # 1. Antrikan command ke mesin
    cmd = await queue_command(
        db=db_session,
        device_sn=sn,
        command_type="DATA UPDATE USERINFO",
        payload="DATA UPDATE USERINFO PIN=888\tName=Testing User",
        idempotency_key="user:888:v1",
    )
    assert cmd.state == "pending"

    # 2. Mesin melakukan polling getrequest
    poll_resp = await client.get(f"/iclock/getrequest?SN={sn}")
    assert poll_resp.status_code == 200
    assert poll_resp.text.startswith(f"C:{cmd.id}:")

    # Pastikan status command berubah menjadi 'sent'
    stmt = select(DeviceCommand).where(DeviceCommand.id == cmd.id)
    updated_cmd = (await db_session.execute(stmt)).scalar_one()
    assert updated_cmd.state == "sent"
    assert updated_cmd.attempts == 1

    # 3. Polling berikutnya harus membalas 'OK' karena antrian sudah kosong
    poll_resp2 = await client.get(f"/iclock/getrequest?SN={sn}")
    assert poll_resp2.status_code == 200
    assert poll_resp2.text.strip() == "OK"

    # 4. Mesin mengirimkan laporan eksekusi sukses (Return=0)
    ack_body = f"ID={cmd.id}&Return=0&CMD=DATA UPDATE USERINFO"
    ack_resp = await client.post(
        f"/iclock/devicecmd?SN={sn}",
        content=ack_body.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
    )
    assert ack_resp.status_code == 200
    assert ack_resp.text.strip() == "OK"

    # Pastikan status di DB berubah menjadi 'acked'
    cmd_after_ack = (await db_session.execute(stmt)).scalar_one()
    assert cmd_after_ack.state == "acked"
    assert cmd_after_ack.acked_at is not None


@pytest.mark.asyncio
async def test_command_supersede_older_pending_version(client: AsyncClient, db_session: AsyncSession):
    """Jika versi baru dari user dibuat, command lama yang masih pending di-supersede."""
    sn = "DEV_CMD_02"

    # Antrikan versi 1
    c1 = await queue_command(
        db=db_session,
        device_sn=sn,
        command_type="DATA UPDATE USERINFO",
        payload="DATA UPDATE USERINFO PIN=999\tName=Nama Lama",
        idempotency_key="user:999:v1",
    )

    # Antrikan versi 2 sebelum versi 1 terkirim
    c2 = await queue_command(
        db=db_session,
        device_sn=sn,
        command_type="DATA UPDATE USERINFO",
        payload="DATA UPDATE USERINFO PIN=999\tName=Nama Baru",
        idempotency_key="user:999:v2",
    )

    # Ambil ulang state c1
    stmt1 = select(DeviceCommand).where(DeviceCommand.id == c1.id)
    c1_updated = (await db_session.execute(stmt1)).scalar_one()

    # c1 harus dibatalkan (cancelled / superseded)
    assert c1_updated.state == "cancelled"
    assert "Superseded" in (c1_updated.last_error or "")

    # c2 harus tetap pending
    stmt2 = select(DeviceCommand).where(DeviceCommand.id == c2.id)
    c2_updated = (await db_session.execute(stmt2)).scalar_one()
    assert c2_updated.state == "pending"


@pytest.mark.asyncio
async def test_command_failed_ack_triggers_retry(client: AsyncClient, db_session: AsyncSession):
    """Jika mesin melaporkan Return!=0, command beralih ke pending dengan jadwal retry."""
    sn = "DEV_CMD_03"
    cmd = await queue_command(
        db=db_session,
        device_sn=sn,
        command_type="DATA UPDATE USERINFO",
        payload="TEST_PAYLOAD",
        idempotency_key="test:fail:v1",
    )

    # Polling
    await client.get(f"/iclock/getrequest?SN={sn}")

    # Mesin kirim Return=-1 (Gagal)
    ack_body = f"ID={cmd.id}&Return=-1&CMD=DATA UPDATE USERINFO"
    await client.post(f"/iclock/devicecmd?SN={sn}", content=ack_body.encode("utf-8"))

    # Cek bahwa command kembali pending dengan last_error terisi
    stmt = select(DeviceCommand).where(DeviceCommand.id == cmd.id)
    cmd_db = (await db_session.execute(stmt)).scalar_one()
    assert cmd_db.state == "pending"
    assert "Return=-1" in (cmd_db.last_error or "")


@pytest.mark.asyncio
async def test_command_sent_timeout_reclaimed_to_pending(client: AsyncClient, db_session: AsyncSession):
    """Command yang berstatus 'sent' tapi tidak mendapat ACK melewati batas waktu dikembalikan ke 'pending'."""
    from datetime import timedelta
    from sqlalchemy import update
    from app.models.models import utcnow

    sn = "DEV_TIMEOUT_01"
    cmd = await queue_command(
        db=db_session,
        device_sn=sn,
        command_type="DATA UPDATE USERINFO",
        payload="TEST_PAYLOAD",
        idempotency_key="test:timeout:v1",
    )

    # Polling oleh mesin (status berubah menjadi 'sent')
    await client.get(f"/iclock/getrequest?SN={sn}")

    # Simulasikan mesin mati / jaringan putus sehingga timeout terlewati
    cmd_stmt = select(DeviceCommand).where(DeviceCommand.id == cmd.id)
    cmd_db = (await db_session.execute(cmd_stmt)).scalar_one()
    cmd_db.next_attempt_at = utcnow() - timedelta(minutes=10)
    await db_session.commit()

    # Eksekusi logika recovery timeout
    now = utcnow()
    reclaim_stmt = (
        update(DeviceCommand)
        .where(
            DeviceCommand.state == "sent",
            DeviceCommand.next_attempt_at <= now,
        )
        .values(
            state="pending",
            last_error="Timeout: tidak ada ACK dalam batas waktu",
        )
    )
    res = await db_session.execute(reclaim_stmt)
    await db_session.commit()
    assert res.rowcount == 1

    # Verifikasi status kembali ke 'pending' dan siap dikirim ulang pada poll berikutnya
    cmd_after = (await db_session.execute(cmd_stmt)).scalar_one()
    assert cmd_after.state == "pending"
    assert "Timeout" in (cmd_after.last_error or "")
