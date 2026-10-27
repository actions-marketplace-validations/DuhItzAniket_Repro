"""Whether a pull request changes only documentation, so CI can skip its slow jobs.

CI's ``changes`` job pipes the pull request's changed files into this script
(``git diff --name-only --no-renames``, so a moved file counts at both of its paths) and adds
the answer to the job's outputs as ``docs_only=true`` or ``docs_only=false``.

The answer is ``true`` only when every path is documentation that nothing builds, tests or
packages. Anything else means the full run: code, tests, workflows, package manifests and
their READMEs (PyPI shows those), lockfiles, a document a test reads, an unknown kind of file,
or no files at all.

    git diff --name-only --no-renames BASE HEAD | python3 -I scripts/ci_changes.py
"""

import sys
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Final

#: Documents that tests read: a change to one of them runs the suites. Every
#: ``"docs" / "<file>"`` a test opens must be here (engine/tests/test_ci_changes.py checks it).
READ_BY_TESTS: Final = frozenset({"docs/ARCHITECTURE.md", "docs/THREAT_MODEL.md"})
#: Files at the repository's root that only people (and GitHub's citation button) read.
ROOT_DOCS: Final = frozenset(
    {
        "CHANGELOG.md",
        "CITATION.cff",
        "CLAUDE.md",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "README.md",
        "SECURITY.md",
    }
)
#: Under docs/: text and pictures. Other files there (the perf records' JSON) are data.
DOC_SUFFIXES: Final = frozenset({".bib", ".gif", ".jpeg", ".jpg", ".md", ".png", ".svg", ".webp"})
#: GitHub's own pages for people: issue forms, the pull request template, the social preview.
GITHUB_DOCS: Final = frozenset({".github/pull_request_template.md", ".github/social-preview.png"})
ISSUE_TEMPLATES: Final = ".github/ISSUE_TEMPLATE/"


def is_documentation(path: str) -> bool:
    """Whether a changed file is documentation that no job builds, tests or packages."""
    if path in READ_BY_TESTS:
        return False
    if path in ROOT_DOCS or path in GITHUB_DOCS:
        return True
    if path.startswith(ISSUE_TEMPLATES):
        return True
    if path.startswith("docs/"):
        return PurePosixPath(path).suffix.lower() in DOC_SUFFIXES
    return False


def docs_only(paths: Iterable[str]) -> bool:
    """Whether every changed file is documentation; no files at all is not."""
    changed = [path.strip() for path in paths if path.strip()]
    return bool(changed) and all(is_documentation(path) for path in changed)


def main() -> None:
    sys.stdout.write(f"docs_only={'true' if docs_only(sys.stdin) else 'false'}\n")


if __name__ == "__main__":
    main()
