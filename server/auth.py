"""Admin login for the back office (/admin): everything that is not the plain MT5-style terminal.

The terminal (`/`) is public on purpose: it only shows the delayed feed and the paper account. Delay, labels, bid/ask
lines, practice calls, Peek (live data), the bot lab and the reports all sit behind this login.

Credentials live in `data/admin.json` (PBKDF2 hash + a signing secret), created on first start with a random password that
is printed once on the console. Set `LAB_ADMIN_PASSWORD` before the first start to choose your own, or run
`python -m tools.set_admin_password` any time to change it. A successful login sets a signed, httpOnly, SameSite=Strict
cookie that lasts 12 hours. Failed logins are rate-limited per client address.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import HTTPException, Request, WebSocket

COOKIE = "lab_admin"
SESSION_SECONDS = 12 * 3600
ITERATIONS = 200_000
MAX_FAILS, FAIL_WINDOW_S = 5, 60


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS).hex()


class AdminAuth:
    def __init__(self, path: Path, now=time.time):
        self.path, self._now = Path(path), now
        self._fails: Dict[str, List[float]] = {}
        self._lock = threading.Lock()
        self.created_password: Optional[str] = None     # set only when this start generated one (printed by the caller)
        if not self.path.exists():
            self._create(os.environ.get("LAB_ADMIN_PASSWORD") or None)
        self.d = json.loads(self.path.read_text())

    # ------------------------------------------------------------------ credentials
    def _create(self, password: Optional[str], username: str = "admin") -> None:
        if password is None:
            password = self.created_password = secrets.token_urlsafe(9)
        salt = secrets.token_bytes(16)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"username": username, "salt": salt.hex(), "hash": _hash(password, salt),
                                         "secret": secrets.token_hex(32)}))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def set_password(path: Path, password: str, username: str = "admin") -> None:
        if len(password) < 8:
            raise ValueError("use at least 8 characters")
        AdminAuth(path)._create(password, username)       # new secret too, so every old session is invalidated

    @property
    def username(self) -> str:
        return self.d["username"]

    def check(self, username: str, password: str) -> bool:
        ok_user = hmac.compare_digest(username.encode(), self.d["username"].encode())
        ok_pw = hmac.compare_digest(_hash(password, bytes.fromhex(self.d["salt"])), self.d["hash"])
        return ok_user and ok_pw

    # ------------------------------------------------------------------ rate limit
    def throttled(self, who: str) -> bool:
        with self._lock:
            now = self._now()
            recent = [t for t in self._fails.get(who, []) if now - t < FAIL_WINDOW_S]
            self._fails[who] = recent
            return len(recent) >= MAX_FAILS

    def record_failure(self, who: str) -> None:
        with self._lock:
            self._fails.setdefault(who, []).append(self._now())

    def clear_failures(self, who: str) -> None:
        with self._lock:
            self._fails.pop(who, None)

    # ------------------------------------------------------------------ tokens
    def _sig(self, body: str) -> str:
        return hmac.new(bytes.fromhex(self.d["secret"]), body.encode(), hashlib.sha256).hexdigest()

    def issue(self) -> str:
        body = base64.urlsafe_b64encode(json.dumps({"u": self.username, "exp": int(self._now()) + SESSION_SECONDS}).encode()).decode()
        return f"{body}.{self._sig(body)}"

    def verify(self, token: Optional[str]) -> bool:
        if not token or "." not in token:
            return False
        body, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sig(body)):
            return False
        try:
            d = json.loads(base64.urlsafe_b64decode(body.encode()))
        except ValueError:
            return False
        return d.get("u") == self.username and d.get("exp", 0) > self._now()


def client_id(request: Request) -> str:
    return request.client.host if request.client else "?"


def require_admin(request: Request) -> None:
    """FastAPI dependency for HTTP routes. A server built with require_admin=False (tests) lets everything through."""
    st = request.app.state
    if not st.require_admin:
        return
    if not st.auth.verify(request.cookies.get(COOKIE)):
        raise HTTPException(401, "admin login required")


def ws_is_admin(ws: WebSocket) -> bool:
    st = ws.app.state
    return (not st.require_admin) or st.auth.verify(ws.cookies.get(COOKIE))
