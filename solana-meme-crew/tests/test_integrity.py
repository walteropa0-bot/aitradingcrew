"""Static guard-rails over the source tree - they catch whole classes of mistakes
(e.g. a config constant that is used but was never defined)."""
import ast
import os
import re

import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def py_files():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in ("tests", ".venv", "venv", "__pycache__", "data", ".git")]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(base, f)


def test_every_config_attribute_used_in_code_exists():
    missing = []
    for path in py_files():
        if path.endswith(os.sep + "config.py") or os.path.basename(path) == "config.py":
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in ("config", "cfg"):
                if not hasattr(config, node.attr):
                    missing.append(f"{os.path.relpath(path, ROOT)}: config.{node.attr}")
    assert not missing, missing


def test_paper_trading_switch_is_the_first_statement_and_defaults_to_safe():
    src = open(os.path.join(ROOT, "config.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    stmts = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant))]
    first = stmts[0]
    assert isinstance(first, ast.Assign) and first.targets[0].id == "PAPER_TRADING" and first.value.value is True
    assert re.search(r"^PAPER_TRADING = True\b", src, re.M)
    first_line = next(i for i, l in enumerate(src.splitlines(), 1) if l.startswith("PAPER_TRADING ="))
    assert first_line < 40, "PAPER_TRADING must stay at the very top of config.py"


def test_every_module_compiles_and_imports():
    import importlib
    for path in py_files():
        rel = os.path.relpath(path, ROOT)[:-3].replace(os.sep, ".")
        if rel in ("main", "selftest", "config"):
            compile(open(path, encoding="utf-8").read(), path, "exec")
            continue
        importlib.import_module(rel)


def test_no_private_keys_or_passwords_are_hardcoded():
    pat = re.compile(r"[1-9A-HJ-NP-Za-km-z]{80,90}")        # base58 secret-key shaped strings
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "data", "tests", ".venv", "static")]
        for f in files:
            if f.endswith((".py", ".md", ".txt", ".example", ".replit", ".ini", ".html", ".js", ".css")):
                text = open(os.path.join(base, f), encoding="utf-8", errors="ignore").read()
                assert not pat.search(text), f"possible key in {f}"
    assert config.SOLANA_PRIVATE_KEY == "" or os.environ.get("SOLANA_PRIVATE_KEY")


def test_code_has_no_fund_transfer_primitives():
    banned = ("TransferParams", "SystemProgram", "system_program", "sendTransaction", "send_raw_transaction")
    for path in py_files():
        text = open(path, encoding="utf-8").read()
        for b in banned:
            assert b not in text, f"{b} found in {os.path.relpath(path, ROOT)}"


def test_requirements_cover_all_third_party_imports():
    reqs = open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8").read().lower()
    for pkg in ("fastapi", "uvicorn", "httpx", "solders", "plotly", "psycopg"):
        assert pkg in reqs
