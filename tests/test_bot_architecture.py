"""Strategy code must not be able to see the future or reach the engine: enforced statically."""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOT_DIR = ROOT / "bot"

# modules that make up a "bot" as the lab runs it: the contract and the strategies
DECISION_MODULES = [BOT_DIR / "interface.py"] + sorted((BOT_DIR / "strategies").glob("*.py"))
FORBIDDEN = (
    "sqlite3", "engine", "server", "collector", "analysis", "tools", "core.db",
    "grader.grader", "grader.labeler_worker",                       # the only readers of the future
    "bot.runner", "bot.evaluate", "bot.live", "bot.run",            # a bot does not get to drive itself
)


def imports(path):
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.Import):
            yield from (a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            yield n.module
            yield from (f"{n.module}.{a.name}" for a in n.names)


def bad(path):
    return sorted({m for m in imports(path) if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)})


def test_decision_modules_cannot_import_anything_that_sees_the_future_or_acts_directly():
    assert len(DECISION_MODULES) >= 4
    found = {p.name: bad(p) for p in DECISION_MODULES if bad(p)}
    assert not found, found


def test_the_detector_would_catch_a_violation(tmp_path):
    f = tmp_path / "evil.py"
    f.write_text("import sqlite3\nfrom grader.grader import grade\nfrom engine.account import Engine\nimport os\n")
    assert bad(f) == ["engine", "engine.account", "grader.grader", "sqlite3"] or {"sqlite3", "grader.grader", "engine.account"} <= set(bad(f))


def test_only_the_post_hoc_tools_may_touch_outcomes():
    """evaluate.py (grading) and run.py (the replay CLI, which calls it and uses a date helper) are the only bot modules that may
    import `analysis`; the runner and the live manager - the code that FEEDS a bot - and the contract itself never do."""
    allowed = {"evaluate.py", "run.py"}
    for p in BOT_DIR.glob("*.py"):
        assert any(m.startswith("analysis") for m in imports(p)) == (p.name in allowed), p.name
    assert not any(m.startswith("grader.grader") or m.startswith("grader.labeler") for p in BOT_DIR.rglob("*.py") for m in imports(p))


def test_strategies_in_the_registry_all_live_in_the_checked_package():
    from bot.strategies import REGISTRY
    mods = {pathlib.Path(__import__(c.__module__, fromlist=["x"]).__file__).name for c in REGISTRY.values()}
    assert mods <= {p.name for p in DECISION_MODULES}
