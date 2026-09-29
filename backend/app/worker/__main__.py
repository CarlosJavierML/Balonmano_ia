"""Standalone queue worker: ``python -m app.worker``.

Run it on the machine that should do the heavy analysis (ideally one with a
GPU), pointing ``BALONMANO_DATABASE_URL`` and the data directory at the same
place as the web server, and set ``BALONMANO_EMBEDDED_WORKER=false`` on the
web server so it only serves the API.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from app.db import init_db
from app.worker.runner import Worker


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await init_db()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await Worker().run(stop)


if __name__ == "__main__":
    asyncio.run(main())
