"""Parser attendance log (ATTLOG) dari ZKTeco Push Protocol."""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class ParsedAttlogRecord:
    pin: str
    punched_at_local: datetime
    status: int
    verify_type: int
    workcode: str
    raw_line: str


@dataclass
class AttlogParseResult:
    records: List[ParsedAttlogRecord]
    malformed_lines: List[Tuple[str, str]]  # (raw_line, error_reason)


def parse_attlog_line(line: str) -> Optional[ParsedAttlogRecord]:
    """Mengurai satu baris ATTLOG. Format umum:
    PIN \t YYYY-MM-DD HH:MM:SS \t Status \t Verify \t Workcode \t Reserved1 \t Reserved2
    """
    clean_line = line.strip("\r\n")
    if not clean_line:
        return None

    # ZKTeco menggunakan tab (\t) sebagai pemisah kolom standar,
    # beberapa firmware memakai spasi ganda bila tab dikonversi
    parts = clean_line.split("\t")
    if len(parts) == 1 and " " in clean_line:
        # Fallback jika separator bukan tab melainkan multi-spasi
        raw_parts = [p.strip() for p in clean_line.split(" ") if p.strip()]
        if len(raw_parts) >= 2:
            # Bagian tanggal dan jam dipisahkan spasi (misal: "101", "2026-11-03", "08:00:15", ...)
            pin = raw_parts[0]
            dt_str = f"{raw_parts[1]} {raw_parts[2]}"
            remaining = raw_parts[3:]
            parts = [pin, dt_str] + remaining

    if len(parts) < 2:
        raise ValueError(f"Jumlah kolom tidak mencukupi (minimal PIN dan Waktu): {clean_line}")

    pin = parts[0].strip()
    dt_str = parts[1].strip()

    # Parse tanggal dan waktu lokal mesin
    # Format umum: YYYY-MM-DD HH:MM:SS
    # Toleransi untuk YYYY/MM/DD atau format 24-jam standar
    punched_at_local: Optional[datetime] = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try:
            punched_at_local = datetime.strptime(dt_str, fmt)
            break
        except ValueError:
            continue

    if punched_at_local is None:
        raise ValueError(f"Format timestamp tidak dikenali: '{dt_str}' pada baris: {clean_line}")

    # Status absensi (0: Masuk, 1: Keluar, dll)
    status = 0
    if len(parts) > 2 and parts[2].strip():
        try:
            status = int(parts[2].strip())
        except ValueError:
            status = 0

    # Metode verifikasi (1: Finger, 2: PIN/Pass, 3: Card, 15: Face)
    verify_type = 0
    if len(parts) > 3 and parts[3].strip():
        try:
            verify_type = int(parts[3].strip())
        except ValueError:
            verify_type = 0

    # Workcode
    workcode = "0"
    if len(parts) > 4 and parts[4].strip():
        workcode = parts[4].strip()

    return ParsedAttlogRecord(
        pin=pin,
        punched_at_local=punched_at_local,
        status=status,
        verify_type=verify_type,
        workcode=workcode,
        raw_line=clean_line,
    )


def parse_attlog_payload(payload_text: str) -> AttlogParseResult:
    """Mengurai keseluruhan isi body POST /iclock/cdata?table=ATTLOG."""
    records: List[ParsedAttlogRecord] = []
    malformed: List[Tuple[str, str]] = []

    lines = payload_text.splitlines()
    for raw_line in lines:
        line_str = raw_line.strip()
        if not line_str:
            continue
        try:
            parsed = parse_attlog_line(line_str)
            if parsed:
                records.append(parsed)
        except Exception as e:
            logger.warning(f"Gagal parse baris ATTLOG: '{line_str}' - Error: {e}")
            malformed.append((line_str, str(e)))

    return AttlogParseResult(records=records, malformed_lines=malformed)
