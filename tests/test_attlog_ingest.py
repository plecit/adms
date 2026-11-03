"""Test Anti-Redudansi ATTLOG & Transactional Integrity (Bagian Paling Kritis)."""
import pytest
from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient

from app.models.models import AttendanceLog, AttendanceLogConflict, RawRequest, OutboxEvent


@pytest.mark.asyncio
async def test_duplicate_batch_repeated_10_times_zero_duplicates(client: AsyncClient, db_session: AsyncSession):
    """Kirim batch yang sama 10x -> jumlah baris attlog tetap sama (idempotensi mutlak)."""
    sn = "SF4A_IDEMP_01"
    payload = (
        "101\t2026-11-03 08:00:00\t0\t15\t0\t0\t0\n"
        "102\t2026-11-03 08:01:00\t0\t1\t0\t0\t0\n"
        "103\t2026-11-03 08:02:00\t0\t15\t0\t0\t0\n"
    )

    # Kirim 10 kali
    for i in range(10):
        resp = await client.post(
            f"/iclock/cdata?SN={sn}&table=ATTLOG&Stamp={i*3}",
            content=payload.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 200
        # Selalu membalas OK:3 karena dari sisi mesin 3 baris aman tersimpan
        assert resp.text.strip() == "OK:3"

    # Periksa total baris di tabel attlog
    stmt = select(func.count()).select_from(AttendanceLog).where(AttendanceLog.device_sn == sn)
    count = (await db_session.execute(stmt)).scalar()
    assert count == 3, f"Diharapkan tepat 3 baris di DB, tetapi ditemukan {count}!"


@pytest.mark.asyncio
async def test_reset_stamp_resend_entire_history_zero_new_rows(client: AsyncClient, db_session: AsyncSession):
    """Stamp mesin direset dan mengirim ulang seluruh riwayat -> nol baris baru."""
    sn = "SF4A_RESET_02"
    batch1 = (
        "201\t2026-11-03 07:30:00\t0\t15\t0\t0\t0\n"
        "202\t2026-11-03 07:35:00\t0\t1\t0\t0\t0\n"
    )
    # Kirim awal
    r1 = await client.post(f"/iclock/cdata?SN={sn}&table=ATTLOG&Stamp=100", content=batch1.encode("utf-8"))
    assert r1.status_code == 200

    # Reset Stamp ke 0 dan kirim ulang data yang sama + 1 data baru
    batch2 = batch1 + "203\t2026-11-03 07:40:00\t0\t15\t0\t0\t0\n"
    r2 = await client.post(f"/iclock/cdata?SN={sn}&table=ATTLOG&Stamp=0", content=batch2.encode("utf-8"))
    assert r2.status_code == 200
    assert r2.text.strip() == "OK:3"

    # Total baris harus tepat 3
    stmt = select(func.count()).select_from(AttendanceLog).where(AttendanceLog.device_sn == sn)
    count = (await db_session.execute(stmt)).scalar()
    assert count == 3


@pytest.mark.asyncio
async def test_conflict_detection_different_status_stored_in_conflicts(client: AsyncClient, db_session: AsyncSession):
    """Kunci sama (SN, PIN, punched_at) namun status/verify berbeda -> simpan ke attlog_conflicts, jangan timpa."""
    sn = "SF4A_CONF_03"
    punch_time = "2026-11-03 09:00:00"

    # Baris pertama: Status 0 (Masuk)
    payload1 = f"301\t{punch_time}\t0\t1\t0\t0\t0\n"
    r1 = await client.post(f"/iclock/cdata?SN={sn}&table=ATTLOG&Stamp=1", content=payload1.encode("utf-8"))
    assert r1.status_code == 200

    # Baris kedua: Waktu dan PIN sama, namun Status 1 (Pulang) dan Verify 15 (Wajah)
    payload2 = f"301\t{punch_time}\t1\t15\t0\t0\t0\n"
    r2 = await client.post(f"/iclock/cdata?SN={sn}&table=ATTLOG&Stamp=2", content=payload2.encode("utf-8"))
    assert r2.status_code == 200

    # Data asli di attlog tidak boleh tertimpa
    att_stmt = select(AttendanceLog).where(AttendanceLog.device_sn == sn, AttendanceLog.pin == "301")
    att = (await db_session.execute(att_stmt)).scalar_one()
    assert att.status == 0  # Tetap status awal

    # Cek bahwa ada entri di attlog_conflicts
    conf_stmt = select(AttendanceLogConflict).where(AttendanceLogConflict.device_sn == sn, AttendanceLogConflict.pin == "301")
    conflict = (await db_session.execute(conf_stmt)).scalar_one_or_none()
    assert conflict is not None
    assert conflict.conflict_data["incoming"]["status"] == 1
    assert conflict.conflict_data["existing"]["status"] == 0


@pytest.mark.asyncio
async def test_malformed_line_does_not_abort_valid_lines(client: AsyncClient, db_session: AsyncSession):
    """Baris rusak tidak menggagalkan baris lain; terlihat di raw_requests.parse_status='partial'."""
    sn = "SF4A_CORRUPT_04"
    payload = (
        "401\t2026-11-03 10:00:00\t0\t1\t0\t0\t0\n"
        "LINE_RUSAK_TOTAL_TANPA_DELIMITER\n"
        "402\t2026-11-03 10:05:00\t0\t1\t0\t0\t0\n"
    )
    resp = await client.post(f"/iclock/cdata?SN={sn}&table=ATTLOG&Stamp=1", content=payload.encode("utf-8"))
    assert resp.status_code == 200
    assert resp.text.strip() == "OK:2"  # 2 baris valid berhasil tersimpan

    # Cek baris valid tersimpan
    stmt = select(func.count()).select_from(AttendanceLog).where(AttendanceLog.device_sn == sn)
    assert (await db_session.execute(stmt)).scalar() == 2

    # Cek status raw_requests adalah 'partial'
    raw_stmt = select(RawRequest).where(RawRequest.device_sn == sn).order_by(RawRequest.id.desc())
    raw_req = (await db_session.execute(raw_stmt)).scalars().first()
    assert raw_req.parse_status == "partial"
    assert "LINE_RUSAK_TOTAL" in (raw_req.parse_error or "")


@pytest.mark.asyncio
async def test_two_different_devices_same_pin_not_deduped(client: AsyncClient, db_session: AsyncSession):
    """Dua mesin dipakai orang yang sama pada waktu yang sama -> bukan duplikat, tersimpan keduanya."""
    punch_time = "2026-11-03 11:00:00"
    line = f"501\t{punch_time}\t0\t1\t0\t0\t0\n"

    # Punch di Mesin A (SenseFace 4A)
    r1 = await client.post("/iclock/cdata?SN=DEVICE_A&table=ATTLOG&Stamp=1", content=line.encode("utf-8"))
    assert r1.status_code == 200

    # Punch di Mesin B (M2-LR)
    r2 = await client.post("/iclock/cdata?SN=DEVICE_B&table=ATTLOG&Stamp=1", content=line.encode("utf-8"))
    assert r2.status_code == 200

    # Kedua punch harus ada di DB
    stmt = select(func.count()).select_from(AttendanceLog).where(AttendanceLog.pin == "501")
    count = (await db_session.execute(stmt)).scalar()
    assert count == 2


@pytest.mark.asyncio
async def test_outbox_event_emitted_only_for_new_rows(client: AsyncClient, db_session: AsyncSession):
    """Outbox event hanya di-generate untuk punch yang benar-benar baru, bukan saat retry duplikat."""
    sn = "SF4A_OUTBOX_05"
    payload = "601\t2026-11-03 12:00:00\t0\t1\t0\t0\t0\n"

    # Kirim pertama kali
    await client.post(f"/iclock/cdata?SN={sn}&table=ATTLOG&Stamp=1", content=payload.encode("utf-8"))
    outbox_stmt = select(func.count()).select_from(OutboxEvent).where(OutboxEvent.event_type == "ATTLOG_CREATED")
    count1 = (await db_session.execute(outbox_stmt)).scalar()
    assert count1 == 1

    # Kirim ulang (retry)
    await client.post(f"/iclock/cdata?SN={sn}&table=ATTLOG&Stamp=1", content=payload.encode("utf-8"))
    count2 = (await db_session.execute(outbox_stmt)).scalar()
    assert count2 == 1  # Tidak bertambah!
