"""The oldest interpreter this package says it installs on, asked whether it could parse it.

`requires-python` is a promise made to a resolver, and until CI ran the suite on that
version it was a promise nothing checked: `pip` believes it, installs on 3.11, and the
failure arrives at import time on somebody else's machine. CI now runs the whole suite on
the floor, which is the real answer. This is the half of it that costs nothing and fails in
a second, on the machine of whoever wrote the syntax: `ast` will parse for an older grammar
if asked, so every source file is parsed for the floor's.

What this catches is syntax — a `type` statement, a PEP 695 generic, whatever 3.14 adds
that is nice and that this cannot use yet. What it cannot catch is a library that grew a
name later, which is what actually blocked 3.10 here: `datetime.UTC` is 3.11, parses
everywhere, and raises on 3.10 at the line that uses it. That is CI's job, and the two
together are why the floor is checked in both places.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "docs").glob("*.py"))


def floor() -> tuple[int, int]:
    """The floor as `pyproject.toml` declares it. Read rather than written down here,
    because a second copy of the number is a second number to move."""
    declared = (ROOT / "pyproject.toml").read_text(encoding="utf8")
    found = re.search(r'^requires-python = ">=(\d+)\.(\d+)"', declared, re.MULTILINE)
    assert found, "pyproject.toml declares no requires-python floor"
    return int(found.group(1)), int(found.group(2))


def test_the_floor_is_declared_and_is_a_version_ast_can_parse_for():
    """`feature_version` only reaches back so far, so a floor below what this test can
    check would make the test pass by doing nothing."""
    major, minor = floor()

    assert major == 3
    assert minor >= 8, "ast cannot parse for a grammar older than 3.8"


@pytest.mark.parametrize("source", SOURCES, ids=lambda path: str(path.relative_to(ROOT)))
def test_every_source_file_parses_on_the_oldest_interpreter_we_claim(source: Path):
    """Every file that ships, and the two scripts in `docs` that a contributor runs, read
    as valid Python at the floor. A syntax error here names the file and the line."""
    try:
        ast.parse(source.read_text(encoding="utf8"), filename=str(source), feature_version=floor()[1])
    except SyntaxError as failure:
        pytest.fail(
            f"{source.relative_to(ROOT)}:{failure.lineno} does not parse on Python "
            f"3.{floor()[1]}, which pyproject.toml says this package installs on: {failure.msg}"
        )
