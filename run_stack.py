"""Start the whole lab with one command (collector, labeler, web server).

    python run_stack.py --sim            # simulated ticks (any OS; no broker needed)
    python run_stack.py                  # real MT5 collector (Windows / bridged)
    python run_stack.py --sim --port 8000 --host 0.0.0.0     # reachable from your phone on the LAN

Ctrl+C stops everything. Build the web app first:  cd web && npm install && npm run build
"""
import argparse
import os
import signal
import subprocess
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--sim", action="store_true", help="simulated tick feed instead of MT5")
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8000)
ap.add_argument("--db", default=None, help="database path (default: from config.yaml)")
args = ap.parse_args()

env = dict(os.environ)
if args.db:
    env["LAB_DB"] = args.db
db = ["--db", args.db] if args.db else []
procs = [
    subprocess.Popen([sys.executable, "-m", "collector.collector"] + (["--sim"] if args.sim else []) + db, env=env),
]
time.sleep(3)                      # let the collector create the db / backfill a little first
procs += [
    subprocess.Popen([sys.executable, "-m", "grader.labeler_worker"] + db, env=env),
    subprocess.Popen([sys.executable, "-m", "uvicorn", "server.app:app", "--host", args.host,
                      "--port", str(args.port), "--log-level", "warning"], env=env),
]
print(f"lab running -> http://{args.host}:{args.port}   (Ctrl+C to stop)", flush=True)


def stop(*_):
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    sys.exit(0)


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
while True:
    for p in procs:
        if p.poll() is not None:
            print(f"process {p.args} exited with {p.returncode}; stopping", flush=True)
            stop()
    time.sleep(1)
