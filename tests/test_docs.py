"""The documents that name files, held to the files they name.

`docs/extending.md` is a list of what a change has to touch, and the objection to writing
one down is that it goes stale: a file moves, the page still names the old path, and the
next person following it looks for something that is not there. That objection is answered
here rather than accepted. Every path the page quotes has to exist, and every link between
the markdown files has to resolve.

What this cannot check is whether the list is still *complete* — a new step nobody wrote
down is invisible to a test that only reads what was written. That is what the mirrors and
the fixture coverage tests are for, and where a step has one, `extending.md` says so.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXTENDING = ROOT / "docs" / "extending.md"

# A backticked token that is a path rather than a name: it has a directory in it, and it
# ends in a file extension or a slash. `catalog.py` and `misreads()` are names of things
# and are deliberately not matched — the page uses them where the file is already obvious
# from its section, and a bare name is not a path to check.
PATHISH = re.compile(r"`([\w./-]+/[\w./-]*(?:\.\w+|/))`")

# A markdown link to something that is not a URL and not an anchor on the page itself.
LINK = re.compile(r"\[[^\]]+\]\((?!https?:|#)([^)#]+)(?:#[^)]*)?\)")

MARKDOWN = sorted(ROOT.glob("*.md")) + sorted((ROOT / "docs").glob("*.md"))


def _named(text: str) -> list[str]:
    return sorted({match.group(1) for match in PATHISH.finditer(text)})


@pytest.mark.parametrize("path", _named(EXTENDING.read_text()))
def test_every_file_extending_names_is_a_file_that_exists(path):
    assert (ROOT / path).exists(), (
        f"docs/extending.md names '{path}', which is not in the repository. "
        "The page is a list of what a change has to touch, so a path that moved makes it "
        "wrong rather than merely out of date."
    )


def test_extending_names_enough_files_to_be_worth_checking():
    """The test above passes trivially if the pattern stops matching. This is what says it
    still matches something, so a rewrite of the page that changes how paths are written
    fails here rather than quietly checking nothing."""
    assert len(_named(EXTENDING.read_text())) > 15


@pytest.mark.parametrize("document", MARKDOWN, ids=lambda p: str(p.relative_to(ROOT)))
def test_a_link_between_documents_resolves(document):
    for target in LINK.findall(document.read_text()):
        assert (document.parent / target).exists(), (
            f"{document.relative_to(ROOT)} links to '{target}', which does not exist"
        )
