"""Hard rule 1, enforced mechanically: only the grader may look past a cutoff."""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = [p for d in ("core", "server", "grader", "collector", "analysis", "engine") for p in (ROOT / d).glob("*.py")]

# modules allowed to import the grader (it can read past any cutoff)
MAY_IMPORT_GRADER = {"grader/grader.py", "grader/labeler_worker.py"}
# modules allowed to query the raw ticks table
MAY_QUERY_TICKS = {
    "core/dataview.py",            # the guarded reader
    "core/db.py",                  # inserts / digits inference
    "grader/grader.py",            # the only module that reads past a cutoff
    "grader/labeler_worker.py",    # drives the grader
    "collector/verify.py",         # compares stored ticks with the broker
}


def rel(p):
    return str(p.relative_to(ROOT))


def imports(path):
    tree = ast.parse(path.read_text())
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            yield from (a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            yield n.module


def test_only_the_labeler_imports_the_grader():
    bad = [(rel(p), m) for p in PY if rel(p) not in MAY_IMPORT_GRADER
           for m in imports(p) if m in ("grader.grader", "grader.labeler_worker")]
    assert not bad, f"decision/display code must not import the grader: {bad}"


def sql_strings(path):
    """String literals that are not docstrings (SQL lives in these)."""
    tree = ast.parse(path.read_text())
    doc_ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            if n.body and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant):
                doc_ids.add(id(n.body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_ids]


def test_only_allowed_modules_query_the_ticks_table_directly():
    pat = re.compile(r"\b(FROM|JOIN)\s+ticks\b", re.I)
    bad = [rel(p) for p in PY if rel(p) not in MAY_QUERY_TICKS
           and any(pat.search(sv) for sv in sql_strings(p))]
    assert not bad, f"raw tick queries outside the allowed modules: {bad}"


def test_the_detector_would_actually_catch_a_violation(tmp_path):
    f = tmp_path / "sneaky.py"
    f.write_text('def f(conn):\n    return conn.execute("SELECT bid FROM ticks WHERE t_ms > 5")\n')
    pat = re.compile(r"\b(FROM|JOIN)\s+ticks\b", re.I)
    assert any(pat.search(x) for x in sql_strings(f))


def test_features_have_no_database_access_of_their_own():
    text = (ROOT / "grader/features.py").read_text()
    assert "sqlite3.connect" not in text and ".execute(" not in text
    from grader.features import Ctx
    assert "conn" not in Ctx.__dataclass_fields__          # a context holds a DataView, not a connection
    for f in ("server/stream.py", "server/peek.py"):
        assert "sqlite3.connect" not in (ROOT / f).read_text()
