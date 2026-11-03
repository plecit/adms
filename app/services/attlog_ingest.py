"""Layanan ingest attendance log (ATTLOG) anti-redundan dan transaksional."""
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Tuple
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.models.models import (
    Device,
    RawRequest,
    AttendanceLog,
    AttendanceLogConflict,
    OutboxEvent,
    utcnow,
)
from app.parsers.attlog import parse_attlog_payload, ParsedAttlogRecord
from app.services.crypto import calculate_sha256

logger = logging.getLogger(__name__)


async def record_raw_request(
    db: AsyncSession,
    method: str,
    path: str,
    device_sn: str | None = None,
    query: str | None = None,
    headers: Dict[str, Any] | None = None,
    body: str | None = None,
) -> int:
    """Menyimpan request mentah ke tabel raw_requests secara append-only dan terpisah."""
    body_sha = calculate_sha256(body or "") if body else None
    raw_req = RawRequest(
        device_sn=device_sn,
        method=method,
        path=path,
        query=query,
        headers=headers,
        body=body,
        body_sha256=body_sha,
        parse_status="pending",
        received_at=utcnow(),
    )
    db.add(raw_req)
    await db.commit()
    await db.refresh(raw_req)
    return raw_req.id


def _get_upsert_stmt(dialect_name: str):
    """Memilih fungsi insert on conflict sesuai dialect database (Postgres / SQLite)."""
    if "sqlite" in dialect_name:
        return sqlite_insert
    return pg_insert


async def ingest_attlog_batch(
    db: AsyncSession,
    device_sn: str,
    raw_request_id: int,
    payload_text: str,
    stamp: str | None = None,
) -> int:
    """Memproses batch ATTLOG dengan prinsip idempotensi penuh (at-least-once).
    
    Mengembalikan total baris yang berhasil dikonsumsi (baru + duplikat valid).
    Membalas respon OK hanya setelah database commit berhasil.
    """
    # 1. Dapatkan informasi mesin & timezone
    res = await db.execute(select(Device).where(Device.serial_number == device_sn))
    device = res.scalar_one_or_none()

    tz_offset = device.tz_offset_minutes if device else settings.DEFAULT_TZ_OFFSET_MINUTES
    tz = timezone(timedelta(minutes=tz_offset))

    # Update metadata mesin
    now = utcnow()
    if device:
        device.last_seen_at = now
        if stamp:
            device.last_attlog_stamp = stamp
    elif settings.ALLOW_UNKNOWN_DEVICES:
        # Otomatis registrasi perangkat baru
        device = Device(
            serial_number=device_sn,
            name=f"Device {device_sn}",
            tz_offset_minutes=tz_offset,
            last_seen_at=now,
            last_attlog_stamp=stamp,
            is_active=True,
        )
        db.add(device)

    # 2. Parse payload
    parse_result = parse_attlog_payload(payload_text)
    records = parse_result.records
    malformed = parse_result.malformed_lines

    if not records and not malformed:
        # Payload kosong
        await db.commit()
        return 0

    dialect_name = db.bind.dialect.name if db.bind else "postgresql"
    insert_func = _get_upsert_stmt(dialect_name)

    # 3. Proses dalam chunk (maksimal 500 baris per batch chunk)
    CHUNK_SIZE = 500
    total_consumed = 0
    newly_inserted_count = 0

    for i in range(0, len(records), CHUNK_SIZE):
        chunk = records[i : i + CHUNK_SIZE]

        # Siapkan data untuk insert
        prepared_rows = []
        for r in chunk:
            # Konversi waktu lokal ke UTC aware secara deterministik
            punched_at_utc = r.punched_at_local.replace(tzinfo=tz).astimezone(timezone.utc)
            row_id = uuid.uuid4()
            prepared_rows.append({
                "id": row_id,
                "device_sn": device_sn,
                "pin": r.pin,
                "punched_at": punched_at_utc,
                "punched_at_local": r.punched_at_local,
                "status": r.status,
                "verify_type": r.verify_type,
                "workcode": r.workcode,
                "raw_line": r.raw_line,
                "raw_request_id": raw_request_id,
                "received_at": now,
                "tz_offset_at_receive": tz_offset,
            })

        # Jalankan INSERT ... ON CONFLICT DO NOTHING RETURNING
        stmt = insert_func(AttendanceLog).values(prepared_rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["device_sn", "pin", "punched_at"]
        ).returning(
            AttendanceLog.id,
            AttendanceLog.pin,
            AttendanceLog.punched_at,
            AttendanceLog.status,
            AttendanceLog.verify_type,
            AttendanceLog.workcode,
        )

        exec_res = await db.execute(stmt)
        inserted_rows = exec_res.fetchall()
        newly_inserted_count += len(inserted_rows)

        # Kumpulkan set kunci yang baru saja masuk
        inserted_keys = {(r[1], r[2]) for r in inserted_rows}  # (pin, punched_at)

        # Buat outbox event untuk baris yang BENAR-BENAR BARU
        for ins in inserted_rows:
            ins_id, ins_pin, ins_time, ins_status, ins_verify, ins_work = ins
            outbox_ev = OutboxEvent(
                id=uuid.uuid4(),
                event_type="ATTLOG_CREATED",
                aggregate_id=str(ins_id),
                idempotency_key=f"attlog:{ins_id}",
                payload={
                    "attlog_id": str(ins_id),
                    "device_sn": device_sn,
                    "pin": ins_pin,
                    "punched_at": ins_time.isoformat(),
                    "status": ins_status,
                    "verify_type": ins_verify,
                    "workcode": ins_work,
                },
                state="pending",
                created_at=now,
            )
            db.add(outbox_ev)

        # 4. Deteksi Konflik pada baris yang TIDAK ter-insert (duplikat)
        # Jika key sama tapi field status/verify/workcode berbeda, simpan ke attlog_conflicts
        if len(inserted_rows) < len(chunk):
            for r in chunk:
                punched_at_utc = r.punched_at_local.replace(tzinfo=tz).astimezone(timezone.utc)
                if (r.pin, punched_at_utc) not in inserted_keys:
                    # Baris ini mengalami konflik / duplikat. Cek apakah atributnya berbeda.
                    check_stmt = select(AttendanceLog).where(
                        AttendanceLog.device_sn == device_sn,
                        AttendanceLog.pin == r.pin,
                        AttendanceLog.punched_at == punched_at_utc,
                    )
                    existing_res = await db.execute(check_stmt)
                    existing_log = existing_res.scalar_one_or_none()

                    if existing_log:
                        is_different = (
                            existing_log.status != r.status
                            or existing_log.verify_type != r.verify_type
                            or existing_log.workcode != r.workcode
                        )
                        if is_different:
                            logger.warning(
                                f"Deteksi konflik data ATTLOG untuk PIN {r.pin} pada {punched_at_utc}. "
                                f"Eksisting: (status={existing_log.status}, verify={existing_log.verify_type}), "
                                f"Masuk: (status={r.status}, verify={r.verify_type})"
                            )
                            conflict_entry = AttendanceLogConflict(
                                id=uuid.uuid4(),
                                existing_attlog_id=existing_log.id,
                                device_sn=device_sn,
                                pin=r.pin,
                                punched_at=punched_at_utc,
                                conflict_data={
                                    "existing": {
                                        "status": existing_log.status,
                                        "verify_type": existing_log.verify_type,
                                        "workcode": existing_log.workcode,
                                    },
                                    "incoming": {
                                        "status": r.status,
                                        "verify_type": r.verify_type,
                                        "workcode": r.workcode,
                                    },
                                },
                                raw_line=r.raw_line,
                                raw_request_id=raw_request_id,
                                received_at=now,
                            )
                            db.add(conflict_entry)

        total_consumed += len(chunk)

    # 5. Update status audit di raw_requests
    raw_status = "partial" if malformed else "success"
    err_msg = "; ".join([f"{l}: {e}" for l, e in malformed]) if malformed else None

    await db.execute(
        update(RawRequest)
        .where(RawRequest.id == raw_request_id)
        .values(parse_status=raw_status, parse_error=err_msg)
    )

    # 6. Commit transaksi secara utuh
    await db.commit()

    logger.info(
        f"[{device_sn}] Berhasil ingest ATTLOG: {total_consumed} baris dikonsumsi "
        f"({newly_inserted_count} baru, {total_consumed - newly_inserted_count} duplikat aman, {len(malformed)} rusak)"
    )

    return total_consumed
