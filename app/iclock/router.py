"""Router endpoint protokol ZKTeco iClock (Push Protocol)."""
import logging
from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.models import Device, utcnow
from app.parsers.options import format_options_response, parse_device_info_string
from app.services.attlog_ingest import record_raw_request, ingest_attlog_batch
from app.services.command_queue import fetch_next_pending_command, process_device_command_ack
from app.services.distribution import handle_operlog_ingest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/iclock", tags=["iClock Protocol"])


def _extract_param(request: Request, *param_names: str) -> Optional[str]:
    """Mengambil parameter query atau form tanpa terpengaruh huruf besar/kecil."""
    for name in param_names:
        for k, v in request.query_params.items():
            if k.lower() == name.lower():
                return v
    return None


@router.get("/cdata")
async def iclock_cdata_get(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handshake awal dan registrasi mesin absensi (GET /iclock/cdata?SN=...&options=all)."""
    sn = _extract_param(request, "sn")
    if not sn:
        return PlainTextResponse("UNKNOWN_SN", status_code=400)

    # Catat audit mentah
    await record_raw_request(
        db=db,
        method="GET",
        path=str(request.url.path),
        device_sn=sn,
        query=str(request.url.query),
        headers=dict(request.headers),
    )

    # Dapatkan atau buat entri mesin
    stmt = select(Device).where(Device.serial_number == sn)
    res = await db.execute(stmt)
    device = res.scalar_one_or_none()

    now = utcnow()
    if not device:
        if not settings.ALLOW_UNKNOWN_DEVICES:
            logger.warning(f"Koneksi ditolak untuk SN tidak terdaftar: {sn}")
            return PlainTextResponse("UNAUTHORIZED_DEVICE", status_code=403)

        device = Device(
            serial_number=sn,
            name=f"Device {sn}",
            tz_offset_minutes=settings.DEFAULT_TZ_OFFSET_MINUTES,
            last_seen_at=now,
            is_active=True,
            capabilities={},
        )
        db.add(device)
    else:
        device.last_seen_at = now
        device.ip_last = request.client.host if request.client else None

    # Simpan info kapabilitas dari parameter query jika ada
    extra_caps = dict(request.query_params)
    if extra_caps:
        cur_caps = dict(device.capabilities or {})
        cur_caps.update(extra_caps)
        device.capabilities = cur_caps

    await db.commit()

    # Bentuk balasan konfigurasi server
    response_text = format_options_response(
        sn=sn,
        stamp=device.last_attlog_stamp or "0",
        op_stamp=device.last_operlog_stamp or "0",
        delay=settings.ICLOCK_DELAY,
        error_delay=settings.ICLOCK_ERROR_DELAY,
        trans_interval=settings.ICLOCK_TRANS_INTERVAL,
        tz_offset_minutes=device.tz_offset_minutes,
        realtime=1,
    )
    return PlainTextResponse(response_text)


@router.post("/cdata")
async def iclock_cdata_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Upload data dari mesin: ATTLOG, OPERLOG, atau ATTPHOTO."""
    sn = _extract_param(request, "sn")
    if not sn:
        return PlainTextResponse("UNKNOWN_SN", status_code=400)

    table = _extract_param(request, "table") or "ATTLOG"
    stamp = _extract_param(request, "stamp")

    # Baca body mentah
    body_bytes = await request.body()
    try:
        body_text = body_bytes.decode("utf-8")
    except UnicodeDecodeError:
        body_text = body_bytes.decode("latin-1", errors="replace")

    # LANGKAH 1: Simpan raw request mentah dan commit segera
    raw_req_id = await record_raw_request(
        db=db,
        method="POST",
        path=str(request.url.path),
        device_sn=sn,
        query=str(request.url.query),
        headers=dict(request.headers),
        body=body_text,
    )

    table_upper = table.upper()

    try:
        if table_upper == "ATTLOG":
            # LANGKAH 2 & 3: Ingest ATTLOG dengan prinsip anti-redundan
            consumed_count = await ingest_attlog_batch(
                db=db,
                device_sn=sn,
                raw_request_id=raw_req_id,
                payload_text=body_text,
                stamp=stamp,
            )
            # Balas OK:<n> HANYA SETELAH commit sukses
            return PlainTextResponse(f"OK:{consumed_count}")

        elif table_upper == "OPERLOG":
            # Ingest operasi pendaftaran user/biometrik
            consumed_count = await handle_operlog_ingest(
                db=db,
                device_sn=sn,
                raw_request_id=raw_req_id,
                payload_text=body_text,
                stamp=stamp,
            )
            return PlainTextResponse(f"OK:{consumed_count}")

        elif table_upper == "ATTPHOTO":
            # Foto absensi (opsional / stub)
            logger.info(f"Menerima upload ATTPHOTO dari {sn} (ukuran {len(body_bytes)} bytes)")
            return PlainTextResponse("OK")

        else:
            logger.info(f"Menerima upload tabel tidak dikenal '{table}' dari {sn}")
            return PlainTextResponse("OK")

    except Exception as e:
        logger.error(f"Error memproses POST cdata table={table} dari {sn}: {e}", exc_info=True)
        # Sesuai aturan: balas HTTP 500 (BUKAN OK) agar mesin mengirim ulang
        raise HTTPException(status_code=500, detail="Internal processing error, please retry")


@router.get("/getrequest")
async def iclock_getrequest(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Heartbeat berkala dan polling antrian command dari mesin."""
    sn = _extract_param(request, "sn")
    if not sn:
        return PlainTextResponse("UNKNOWN_SN", status_code=400)

    # Catat last seen & kapabilitas jika mesin mengirim parameter INFO
    stmt = select(Device).where(Device.serial_number == sn)
    dev_res = await db.execute(stmt)
    device = dev_res.scalar_one_or_none()

    now = utcnow()
    if device:
        device.last_seen_at = now
        device.ip_last = request.client.host if request.client else None

        info_param = _extract_param(request, "info")
        if info_param:
            parsed_caps = parse_device_info_string(info_param)
            if parsed_caps:
                cur_caps = dict(device.capabilities or {})
                cur_caps.update(parsed_caps)
                device.capabilities = cur_caps
                # Update model/firmware jika tertera di info
                if "DeviceName" in parsed_caps and not device.model:
                    device.model = parsed_caps["DeviceName"]
                if "FirmwareVersion" in parsed_caps and not device.firmware:
                    device.firmware = parsed_caps["FirmwareVersion"]

        await db.commit()

    # Ambil antrian command dengan FOR UPDATE SKIP LOCKED
    cmd = await fetch_next_pending_command(db, device_sn=sn)
    if cmd:
        logger.info(f"Mengirim command C:{cmd.id} ke mesin {sn}: {cmd.command_type}")
        return PlainTextResponse(f"C:{cmd.id}:{cmd.payload}")

    # Jika antrian kosong, balas OK
    return PlainTextResponse("OK")


@router.post("/devicecmd")
async def iclock_devicecmd(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Pelaporan hasil eksekusi command oleh mesin: ID=<cmd_id>&Return=<kode>&CMD=<nama>."""
    sn = _extract_param(request, "sn")
    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8", errors="replace")

    # Simpan raw audit
    await record_raw_request(
        db=db,
        method="POST",
        path=str(request.url.path),
        device_sn=sn,
        query=str(request.url.query),
        headers=dict(request.headers),
        body=body_text,
    )

    # Parse parameter baik dari body (format form/x-www-form-urlencoded atau query)
    cmd_id: Optional[int] = None
    return_code: Optional[int] = None
    cmd_name: Optional[str] = None

    # Parse key-value dari string: ID=1&Return=0&CMD=...
    combined_pairs = {}
    for part in (str(request.url.query) + "&" + body_text).split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            combined_pairs[k.strip().lower()] = v.strip()

    id_str = combined_pairs.get("id")
    ret_str = combined_pairs.get("return")
    cmd_name = combined_pairs.get("cmd")

    if id_str:
        try:
            cmd_id = int(id_str)
        except ValueError:
            cmd_id = None

    if ret_str is not None:
        try:
            return_code = int(ret_str)
        except ValueError:
            return_code = -1

    if cmd_id is not None and return_code is not None and sn:
        await process_device_command_ack(
            db=db,
            device_sn=sn,
            cmd_id=cmd_id,
            return_code=return_code,
            cmd_name=cmd_name,
        )

    return PlainTextResponse("OK")
