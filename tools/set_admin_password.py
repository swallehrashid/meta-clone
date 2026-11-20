"""Change the back-office (/admin) password:  python -m tools.set_admin_password   (prompts; or LAB_ADMIN_PASSWORD=... env)."""
import getpass
import os
import sys
from pathlib import Path

from server.auth import AdminAuth

path = Path(os.environ.get("LAB_ADMIN_FILE") or Path(__file__).resolve().parent.parent / "data" / "admin.json")
pw = os.environ.get("LAB_ADMIN_PASSWORD") or getpass.getpass("New admin password (8+ chars): ")
if not os.environ.get("LAB_ADMIN_PASSWORD") and getpass.getpass("Again: ") != pw:
    sys.exit("passwords differ")
AdminAuth.set_password(path, pw)
print(f"admin password updated ({path}); all existing admin sessions are signed out")
