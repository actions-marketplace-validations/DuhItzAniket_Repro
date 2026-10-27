"""CI's documentation fast path (scripts/ci_changes.py): which pull requests skip the slow jobs.

A pull request that changes only documentation skips the test suites, the gates and the
sandbox jobs. Getting this wrong in one direction wastes time; in the other it merges code
nobody tested. So everything that is not plainly documentation runs in full, and a document
that a test reads is never plainly documentation.
"""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci_changes.py"
#: Where tests live; each is scanned for the documents it opens.
TEST_DIRS = (ROOT / "engine" / "tests", ROOT / "packages" / "events" / "python" / "tests")
_OPENED_DOC = re.compile(r"""["']docs["']\s*/\s*["']([^"']+)["']""")


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_changes", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci_changes = load()


@pytest.mark.parametrize(
    "paths",
    [
        ["README.md"],
        ["CHANGELOG.md", "docs/phases/release-1.5.0.md", "docs/ROADMAP.md"],
        ["docs/guide/demo.md", "docs/guide/demo/card.png"],
        ["docs/paper/references.bib", "docs/paper/LOG.md"],
        ["CODE_OF_CONDUCT.md", "SECURITY.md", "CONTRIBUTING.md", "CITATION.cff", "CLAUDE.md"],
        [".github/ISSUE_TEMPLATE/bug_report.yml", ".github/pull_request_template.md"],
        [".github/social-preview.png"],
        ["docs/FAILURES.md"],  # a list for people; no test opens it
    ],
)
def test_documentation_alone_takes_the_fast_path(paths: list[str]) -> None:
    assert ci_changes.docs_only(paths)


@pytest.mark.parametrize(
    "paths",
    [
        [],  # nothing changed, or the list could not be made: run everything
        ["engine/src/repro/cli.py"],
        ["README.md", "engine/src/repro/cli.py"],
        # Read by tests.
        ["docs/ARCHITECTURE.md"],
        ["docs/THREAT_MODEL.md"],
        # Markdown that is code or a package's page.
        ["engine/src/repro/agents/prompts/reviewer.md"],
        ["bench/src/repro_bench/prompts/difftest.md"],
        ["engine/PYPI.md"],
        ["engine/README.md"],
        ["packages/events/python/README.md"],
        # Data, configuration and CI itself.
        ["docs/perf/E01-tracer-bench.jsonl"],
        ["docs/perf/B05-web-bench.json"],
        [".github/workflows/ci.yml"],
        [".github/dependabot.yml"],
        ["scripts/ci_changes.py"],
        ["pyproject.toml"],
        ["uv.lock"],
        ["LICENSE"],
        ["apps/web/e2e/__screenshots__/cockpit-12000.png"],  # visual baselines are tested
        ["docs/guide/demo.sh"],  # an unknown kind of file under docs/
    ],
)
def test_anything_else_runs_everything(paths: list[str]) -> None:
    assert not ci_changes.docs_only(paths)


def test_a_file_moved_out_of_the_code_counts_at_both_paths() -> None:
    """With ``--no-renames``, git lists a moved file's old path too: moving code into docs/
    is not a documentation change."""
    assert not ci_changes.docs_only(["engine/src/repro/old.py", "docs/old.md"])


def test_blank_lines_and_line_ends_are_ignored() -> None:
    assert ci_changes.docs_only(["README.md\r\n", "\n", "  docs/a.md  \n"])


def test_every_document_a_test_opens_runs_the_suites() -> None:
    """A test that starts reading a document must add it to READ_BY_TESTS; otherwise a change
    to that document could skip the very test that checks it."""
    opened = {
        f"docs/{match}"
        for directory in TEST_DIRS
        for path in directory.glob("test_*.py")
        for match in _OPENED_DOC.findall(path.read_text(encoding="utf-8"))
    }
    assert opened, "no test opens a document: the scan no longer finds them"
    assert opened <= ci_changes.READ_BY_TESTS, opened - ci_changes.READ_BY_TESTS


def test_the_script_answers_in_the_form_github_outputs_take() -> None:
    def answer(text: str) -> str:
        completed = subprocess.run(
            [sys.executable, "-I", str(SCRIPT)],
            input=text,
            capture_output=True,
            text=True,
            check=True,
        )
        return completed.stdout

    assert answer("README.md\ndocs/guide/demo.md\n") == "docs_only=true\n"
    assert answer("README.md\nengine/src/repro/cli.py\n") == "docs_only=false\n"
    assert answer("") == "docs_only=false\n"
