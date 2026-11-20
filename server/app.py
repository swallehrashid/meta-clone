"""FastAPI app factory.  Run:  uvicorn server.app:app --host 0.0.0.0 --port 8000"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Optional

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse

from core.candles import CandleCache
from core.config import Config, load_config
from fastapi.staticfiles import StaticFiles

from contextlib import asynccontextmanager
import asyncio

from engine.account import Engine
from core import db
from bot.live import BotManager
from server import api_account, api_admin_lab, api_bot, api_calls, api_labels, api_reports, api_terminal, peek, settings, stream
from server.auth import AdminAuth, require_admin


WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def create_app(cfg: Optional[Config] = None, db_path: Optional[str | Path] = None,
               clock: Optional[Callable[[], int]] = None,
               interval_s: Optional[float] = None,
               web_dist: Optional[Path] = WEB_DIST, clock_interval: Optional[float] = None,
               require_admin_login: bool = False) -> FastAPI:
    cfg = cfg or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # the paper account lives for the life of the server; its clock is advanced in the background
        app.state.engine = Engine(cfg, db.connect(app.state.db_path, check_same_thread=False))
        c = db.connect(app.state.db_path)
        try:
            app.state.engine.set_delay(settings.load(cfg, c)["delay_s"])     # the back office decides how far behind the terminal runs
        finally:
            c.close()
        if require_admin_login:
            app.state.auth = AdminAuth(Path(app.state.db_path).parent / "admin.json")
            if app.state.auth.created_password:
                print(f"\n  ADMIN LOGIN for /admin  ->  username: {app.state.auth.username}   password: {app.state.auth.created_password}"
                      "\n  (shown once; change it with: python -m tools.set_admin_password)\n", flush=True)
        app.state.bots = BotManager(cfg, app.state.engine, app.state.clock)          # resumes a bot that was running before a restart
        task = asyncio.create_task(api_account.run_clock(app, cfg.engine.advance_interval_s if clock_interval is None else clock_interval))
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(title="Delayed-Feed Scalping Lab", lifespan=lifespan)
    st = app.state
    st.cfg = cfg
    st.db_path = str(db_path or cfg.resolve_db_path())
    st.clock = clock or (lambda: int(time.time() * 1000))
    st.interval_s = interval_s if interval_s is not None else cfg.server.stream_interval_ms / 1000
    st.require_admin = require_admin_login
    st.quote_cache = {}               # (symbol, day start) -> running low / high for the Quotes screen
    st.auth = None
    st.cache = CandleCache()          # candles are never stored; cached in memory only

    @app.get("/health")
    def health():
        return {"ok": True, "now_ms": st.clock()}

    @app.get("/api/config")
    def public_config():
        return {"symbols": cfg.broker_symbols, "timeframes": cfg.candles.timeframes,
                "delay": cfg.delay.model_dump(), "display_timezone": cfg.display_timezone,
                "price_side": cfg.candles.price_side}

    admin = [Depends(require_admin)]                 # back-office only: labels, calls, reports, bot control, the bot lab
    app.include_router(stream.make_router())
    app.include_router(peek.make_router())           # checks the admin cookie itself (websocket)
    app.include_router(api_terminal.make_router())
    app.include_router(api_labels.make_router(), dependencies=admin)
    app.include_router(api_calls.make_router(), dependencies=admin)
    app.include_router(api_reports.make_router(), dependencies=admin)
    app.include_router(api_account.make_router())
    app.include_router(api_bot.make_router(), dependencies=admin)
    app.include_router(api_admin_lab.make_router(), dependencies=admin)
    api_account.install_error_handler(app)
    # the built web app is served last so it can never shadow an API route
    if web_dist and Path(web_dist).is_dir():
        @app.get("/admin", include_in_schema=False)
        @app.get("/admin/", include_in_schema=False)
        def admin_page():                            # same bundle; main.tsx picks the back office by path
            return FileResponse(Path(web_dist) / "index.html")

        app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="web")
    return app


app = create_app(db_path=os.environ.get("LAB_DB") or None, require_admin_login=True)   # uvicorn server.app:app
