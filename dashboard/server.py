"""FastAPI server wiring the stock-bot dashboard together.

Runs locally on 127.0.0.1:8765 only. All IB interaction goes through the
read-only ``IBMonitor`` snapshot; handlers never touch IB directly and
there are no endpoints that place, modify or cancel orders.
"""

from __future__ import annotations

import atexit
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from dashboard.config_editor import read_momo_config, write_momo_config
from dashboard.ib_monitor import IBMonitor
from dashboard.logs import tail_log
from dashboard.process_manager import BotManager

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"

HOST = "127.0.0.1"
PORT = 8765


def create_app(bot_manager: BotManager | None = None,
               ib_monitor: IBMonitor | None = None) -> FastAPI:
    bot_manager = bot_manager or BotManager()
    ib_monitor = ib_monitor or IBMonitor()

    app = FastAPI(title="Stock Bot Dashboard", docs_url=None, redoc_url=None)

    @app.get("/")
    def index():
        # resolved at request time so the module imports without the file
        if INDEX_HTML.exists():
            return FileResponse(str(INDEX_HTML))
        raise HTTPException(status_code=404, detail="frontend not built")

    @app.get("/api/bots")
    def get_bots():
        return bot_manager.status()

    @app.post("/api/bots/{bot_id}/start")
    def start_bot(bot_id: str):
        try:
            return bot_manager.start(bot_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/bots/{bot_id}/stop")
    def stop_bot(bot_id: str):
        try:
            return bot_manager.stop(bot_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/ib")
    def get_ib():
        return ib_monitor.snapshot()

    @app.get("/api/logs/{name}")
    def get_logs(name: str, lines: int = Query(200, ge=1, le=10000)):
        try:
            return tail_log(name, lines=lines)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/config/momo")
    def get_momo_config():
        try:
            return read_momo_config()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.put("/api/config/momo")
    def put_momo_config(body: dict):
        try:
            return write_momo_config(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # expose for tests / main
    app.state.bot_manager = bot_manager
    app.state.ib_monitor = ib_monitor
    return app


def main() -> None:
    bot_manager = BotManager()
    ib_monitor = IBMonitor()
    ib_monitor.start()

    atexit.register(bot_manager.shutdown)
    atexit.register(ib_monitor.stop)

    app = create_app(bot_manager=bot_manager, ib_monitor=ib_monitor)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
