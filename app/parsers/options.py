"""Parser dan generator blok konfigurasi handshake iClock (cdata options)."""
from typing import Dict, Any


def format_options_response(
    sn: str,
    stamp: str = "0",
    op_stamp: str = "0",
    delay: int = 30,
    error_delay: int = 30,
    trans_interval: int = 1,
    tz_offset_minutes: int = 420,
    realtime: int = 1,
) -> str:
    """Menghasilkan blok respon konfigurasi untuk permintaan options=all dari mesin.
    
    Format respon ZKTeco iClock adalah baris teks diawali dengan 'GET OPTION FROM: <SN>'.
    """
    lines = [
        f"GET OPTION FROM: {sn}",
        f"Stamp={stamp}",
        f"OpStamp={op_stamp}",
        "PhotoStamp=0",
        f"ErrorDelay={error_delay}",
        f"Delay={delay}",
        "TransTimes=00:00;14:05",
        f"TransInterval={trans_interval}",
        "TransFlag=TransData AttLog\tOpLog\tAttPhoto\tEnrollUser\tChgUser\tEnrollFP\tChgFP\tFace",
        f"Realtime={realtime}",
        "Encrypt=0",
        "ServerVersion=3.1.1",
        f"TimeZone={tz_offset_minutes}",
    ]
    return "\n".join(lines) + "\n"


def parse_device_info_string(info_str: str) -> Dict[str, Any]:
    """Mengurai string informasi perangkat dari parameter INFO atau query.
    
    Contoh: 'DeviceName=SenseFace 4A,FirmwareVersion=Ver 2.4.1,FingerCount=500,FaceCount=1000'
    """
    capabilities: Dict[str, Any] = {}
    if not info_str:
        return capabilities

    # Pisahkan baik dengan koma atau baris baru
    parts = info_str.replace("\n", ",").replace("\t", ",").split(",")
    for part in parts:
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            capabilities[k.strip()] = v.strip()
    return capabilities
