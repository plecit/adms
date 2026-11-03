"""Skrip untuk inisialisasi tabel database ADMS."""
import asyncio
import logging
from app.db.session import init_models, engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Memulai pembuatan skema database ADMS...")
    await init_models(engine)
    logger.info("Skema database berhasil dibuat!")


if __name__ == "__main__":
    asyncio.run(main())
