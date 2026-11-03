"""Parser operation log (OPERLOG) dari ZKTeco Push Protocol."""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class ParsedOperlogUser:
    pin: str
    name: str
    privilege: int
    card_no: Optional[str]
    password: Optional[str]
    raw_line: str


@dataclass
class ParsedOperlogBiometric:
    pin: str
    kind: str  # 'finger' atau 'face'
    slot: int  # FID atau Index
    format_version: str
    template_b64: str
    type_code: int  # 1: FP, 2: NIR Face, 9: Visible Light Face
    raw_line: str


@dataclass
class OperlogParseResult:
    users: List[ParsedOperlogUser]
    biometrics: List[ParsedOperlogBiometric]
    others: List[Dict[str, Any]]
    malformed_lines: List[Tuple[str, str]]


def _parse_key_value_line(line: str) -> Tuple[str, Dict[str, str]]:
    """Memisahkan header (USER/FP/BIODATA) dan pasangan key=value."""
    line = line.strip("\r\n")
    if not line:
        return "", {}

    # Ambil token pertama sebagai command/kategori
    parts = line.split(None, 1)
    if not parts:
        return "", {}

    category = parts[0].upper()
    rest = parts[1] if len(parts) > 1 else ""

    kv_dict: Dict[str, str] = {}
    # Protokol ZKTeco menggunakan tab (\t) sebagai pemisah antar field
    if "\t" in rest:
        tokens = [t.strip() for t in rest.split("\t") if t.strip()]
        for token in tokens:
            if "=" in token:
                k, v = token.split("=", 1)
                kv_dict[k.strip().lower()] = v.strip()
    else:
        # Fallback jika firmware mengirimkan pemisah spasi
        current_key = None
        for word in rest.split():
            if "=" in word:
                k, v = word.split("=", 1)
                current_key = k.strip().lower()
                kv_dict[current_key] = v.strip()
            elif current_key:
                # Nilai lanjutan (misal spasi pada nama: 'Name=Ahmad' lalu 'Dahlan')
                kv_dict[current_key] += f" {word}"

    return category, kv_dict


def parse_operlog_payload(payload_text: str) -> OperlogParseResult:
    """Mengurai isi tabel OPERLOG dari request mesin."""
    users: List[ParsedOperlogUser] = []
    biometrics: List[ParsedOperlogBiometric] = []
    others: List[Dict[str, Any]] = []
    malformed: List[Tuple[str, str]] = []

    for raw_line in payload_text.splitlines():
        line_str = raw_line.strip()
        if not line_str:
            continue

        try:
            category, kv = _parse_key_value_line(line_str)
            if not category:
                continue

            if category == "USER":
                pin = kv.get("pin", "")
                if not pin:
                    malformed.append((line_str, "Missing PIN"))
                    continue
                name = kv.get("name", f"User {pin}")
                try:
                    pri = int(kv.get("pri", 0))
                except ValueError:
                    pri = 0
                card = kv.get("card", "") or None
                pwd = kv.get("passwd", "") or None

                users.append(ParsedOperlogUser(
                    pin=pin,
                    name=name,
                    privilege=pri,
                    card_no=card,
                    password=pwd,
                    raw_line=line_str,
                ))

            elif category == "FP":
                pin = kv.get("pin", "")
                tmp_b64 = kv.get("tmp", "")
                if not pin or not tmp_b64:
                    malformed.append((line_str, "Missing PIN or TMP in FP"))
                    continue
                try:
                    fid = int(kv.get("fid", 0))
                except ValueError:
                    fid = 0
                biometrics.append(ParsedOperlogBiometric(
                    pin=pin,
                    kind="finger",
                    slot=fid,
                    format_version="ZKFP10",
                    template_b64=tmp_b64,
                    type_code=1,
                    raw_line=line_str,
                ))

            elif category == "FACE":
                pin = kv.get("pin", "")
                tmp_b64 = kv.get("tmp", "")
                if not pin or not tmp_b64:
                    malformed.append((line_str, "Missing PIN or TMP in FACE"))
                    continue
                try:
                    fid = int(kv.get("fid", 0))
                except ValueError:
                    fid = 0
                biometrics.append(ParsedOperlogBiometric(
                    pin=pin,
                    kind="face",
                    slot=fid,
                    format_version="ZKFACE7",
                    template_b64=tmp_b64,
                    type_code=2,
                    raw_line=line_str,
                ))

            elif category == "BIODATA":
                pin = kv.get("pin", "")
                tmp_b64 = kv.get("tmp", "")
                if not pin or not tmp_b64:
                    malformed.append((line_str, "Missing PIN or TMP in BIODATA"))
                    continue
                try:
                    slot = int(kv.get("no", kv.get("index", 0)))
                except ValueError:
                    slot = 0
                try:
                    type_code = int(kv.get("type", 9))
                except ValueError:
                    type_code = 9

                # Type 9 = Visible Light Face (SenseFace 4A), Type 1 = Finger
                kind = "face" if type_code in (9, 2) else "finger"
                major_ver = kv.get("majorver", "12")
                minor_ver = kv.get("minorver", "0")
                format_ver = f"{major_ver}.{minor_ver}"

                biometrics.append(ParsedOperlogBiometric(
                    pin=pin,
                    kind=kind,
                    slot=slot,
                    format_version=format_ver,
                    template_b64=tmp_b64,
                    type_code=type_code,
                    raw_line=line_str,
                ))

            else:
                others.append({"category": category, "data": kv, "raw": line_str})

        except Exception as e:
            logger.warning(f"Gagal parse baris OPERLOG: '{line_str}' - Error: {e}")
            malformed.append((line_str, str(e)))

    return OperlogParseResult(
        users=users,
        biometrics=biometrics,
        others=others,
        malformed_lines=malformed,
    )
