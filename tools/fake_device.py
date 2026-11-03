"""Simulator Mesin Absensi ZKTeco (SenseFace 4A & M2-LR) untuk Pengujian Idempotensi & Protokol."""
import argparse
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict, Any
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [FakeDevice]: %(message)s")
logger = logging.getLogger("FakeDevice")


class FakeDeviceClient:
    """Klien simulator ZKTeco Push Protocol (iClock)."""

    def __init__(
        self,
        server_url: str = "http://localhost:80",
        serial_number: str = "SF4A23450001",
        model: str = "SenseFace 4A",
        firmware: str = "Ver 2.4.1",
        capabilities: Optional[Dict[str, str]] = None,
    ):
        self.server_url = server_url.rstrip("/")
        self.serial_number = serial_number
        self.model = model
        self.firmware = firmware
        self.stamp = 0
        self.op_stamp = 0
        self.capabilities = capabilities or {
            "DeviceName": model,
            "FirmwareVersion": firmware,
            "FaceCount": "1000",
            "FingerCount": "2000",
            "UserCount": "500",
            "pushver": "2.4.1",
            "language": "83",
        }

    async def handshake(self) -> str:
        """Melakukan handshake awal: GET /iclock/cdata?SN=...&options=all."""
        url = f"{self.server_url}/iclock/cdata"
        params = {
            "SN": self.serial_number,
            "options": "all",
            "pushver": self.capabilities.get("pushver", "2.4.1"),
            "language": "83",
            "pushprotver": "2.4.1",
        }
        params.update(self.capabilities)

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            logger.info(f"Handshake sukses untuk {self.serial_number}. Respon server:\n{resp.text.strip()}")
            return resp.text

    def build_attlog_payload(
        self,
        records: List[Tuple[str, datetime, int, int, str]],
    ) -> str:
        """Menyusun payload baris ATTLOG dipisahkan oleh \\t dan \\n.
        
        Format: PIN \\t YYYY-MM-DD HH:MM:SS \\t Status \\t Verify \\t Workcode \\t 0 \\t 0
        """
        lines = []
        for pin, dt, status, verify, workcode in records:
            dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            line = f"{pin}\t{dt_str}\t{status}\t{verify}\t{workcode}\t0\t0"
            lines.append(line)
        return "\n".join(lines) + "\n"

    async def send_attlog(
        self,
        records: List[Tuple[str, datetime, int, int, str]],
        custom_stamp: Optional[int] = None,
    ) -> str:
        """Mengirim batch log kehadiran ke server. Mengharapkan balasan OK:<n>."""
        if custom_stamp is not None:
            self.stamp = custom_stamp
        else:
            self.stamp += len(records)

        payload = self.build_attlog_payload(records)
        url = f"{self.server_url}/iclock/cdata"
        params = {
            "SN": self.serial_number,
            "table": "ATTLOG",
            "Stamp": str(self.stamp),
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                params=params,
                content=payload.encode("utf-8"),
                headers={"Content-Type": "text/plain"},
            )
            resp.raise_for_status()
            logger.info(f"Kirim ATTLOG ({len(records)} baris) ke server. Respon: {resp.text.strip()}")
            return resp.text

    async def send_attlog_with_dropped_response(
        self,
        records: List[Tuple[str, datetime, int, int, str]],
    ) -> None:
        """Simulasi kegagalan: request dikirim ke server, namun koneksi diputus sebelum membaca respon."""
        payload = self.build_attlog_payload(records)
        url = f"{self.server_url}/iclock/cdata?SN={self.serial_number}&table=ATTLOG&Stamp={self.stamp}"
        
        try:
            # Gunakan timeout sangat pendek agar klien menutup socket seketika setelah kirim payload
            async with httpx.AsyncClient(timeout=0.005) as client:
                await client.post(
                    url,
                    content=payload.encode("utf-8"),
                    headers={"Content-Type": "text/plain"},
                )
        except (httpx.TimeoutException, httpx.RequestError):
            logger.info("Simulasi pemutusan koneksi (drop connection sebelum respon OK terbaca) berhasil dipicu")

    async def send_duplicate_batches(
        self,
        records: List[Tuple[str, datetime, int, int, str]],
        times: int = 10,
    ) -> List[str]:
        """Mengirimkan batch identik berulang kali untuk menguji idempotensi."""
        responses = []
        for i in range(times):
            logger.info(f"Mengirim batch duplikat #{i+1} dari {times}...")
            res = await self.send_attlog(records, custom_stamp=self.stamp)
            responses.append(res)
        return responses

    async def poll_commands_and_ack(self, auto_ack: bool = True) -> Optional[str]:
        """Heartbeat berkala dan eksekusi command:
        1. GET /iclock/getrequest?SN=...
        2. Jika menerima C:<id>:<CMD>, eksekusi dan kirim ACK via POST /iclock/devicecmd.
        """
        url = f"{self.server_url}/iclock/getrequest"
        params = {
            "SN": self.serial_number,
            "INFO": f"DeviceName={self.model},FirmwareVersion={self.firmware}",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            text = resp.text.strip()

            if text.startswith("C:"):
                # Format: C:<cmd_id>:<payload>
                parts = text.split(":", 2)
                cmd_id = parts[1]
                cmd_payload = parts[2] if len(parts) > 2 else ""
                logger.info(f"Menerima COMMAND C:{cmd_id} dari server: {cmd_payload}")

                if auto_ack:
                    ack_url = f"{self.server_url}/iclock/devicecmd"
                    ack_params = {"SN": self.serial_number}
                    ack_body = f"ID={cmd_id}&Return=0&CMD={cmd_payload.split()[0]}"
                    ack_resp = await client.post(
                        ack_url,
                        params=ack_params,
                        content=ack_body.encode("utf-8"),
                        headers={"Content-Type": "text/plain"},
                    )
                    logger.info(f"ACK terkirim untuk C:{cmd_id}. Respon server: {ack_resp.text.strip()}")
                return text

            return None


def generate_sample_punches(
    pins: List[str],
    base_time: Optional[datetime] = None,
    count_per_pin: int = 2,
) -> List[Tuple[str, datetime, int, int, str]]:
    """Helper untuk menghasilkan sampel data absensi realistis."""
    now = base_time or datetime.now()
    records = []
    for pin in pins:
        for i in range(count_per_pin):
            punch_time = now + timedelta(minutes=i * 30)
            status = 0 if i % 2 == 0 else 1  # 0: Masuk, 1: Pulang
            verify = 15 if int(pin) % 2 == 0 else 1  # Face atau Finger
            records.append((pin, punch_time, status, verify, "0"))
    return records


async def cli_main():
    parser = argparse.ArgumentParser(description="Simulator Mesin Absensi ZKTeco Push Protocol")
    parser.add_argument("--server", default="http://localhost:80", help="Alamat server ADMS")
    parser.add_argument("--sn", default="SF4A23450001", help="Serial Number mesin")
    parser.add_argument("--model", default="SenseFace 4A", help="Model mesin")
    parser.add_argument("--action", choices=["handshake", "send_punches", "duplicate_test", "poll"], default="send_punches")
    parser.add_argument("--count", type=int, default=10, help="Jumlah log absensi yang dibuat")
    args = parser.parse_args()

    client = FakeDeviceClient(
        server_url=args.server,
        serial_number=args.sn,
        model=args.model,
    )

    if args.action == "handshake":
        await client.handshake()
    elif args.action == "send_punches":
        records = generate_sample_punches(["101", "102", "103"], count_per_pin=args.count // 3)
        await client.send_attlog(records)
    elif args.action == "duplicate_test":
        records = generate_sample_punches(["201", "202"], count_per_pin=2)
        await client.send_duplicate_batches(records, times=5)
    elif args.action == "poll":
        await client.poll_commands_and_ack()


if __name__ == "__main__":
    asyncio.run(cli_main())
