"""Repro's sandbox images, built on demand, each tagged by its Dockerfile's digest.

- ``image/Dockerfile`` (D03): Python with uv, for Python projects (``sandbox_image``).
- ``image/node.Dockerfile`` (K11): Node.js with npm, pnpm and Yarn, for JavaScript and
  TypeScript projects (``node_image``).

A build that fails is reported in its failing step's own words (``build_failure``). Docker's
last line only repeats the step's whole command and its exit code.
"""

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Final

from repro.readable import without_home
from repro.sandbox.process import SandboxError

DOCKERFILE: Final = Path(__file__).with_name("image") / "Dockerfile"
NODE_DOCKERFILE: Final = Path(__file__).with_name("image") / "node.Dockerfile"
#: Building pulls the base image and Debian packages: minutes on a slow network.
BUILD_TIMEOUT_S: Final = 1800.0
#: The longest cause a failed build reports: the card's detail holds about 300 characters.
CAUSE_MAX: Final = 200
_HEAD: Final = 60  # of a long error chain kept before its end: what failed

#: BuildKit's plain progress, which ``docker build`` writes to stderr when that is not a
#: terminal: a step's output (``#5 12.34 E: Unable to locate package git``), and the line that
#: ends a step that failed (``#5 ERROR: process "..." did not complete successfully: ...``).
_OUTPUT: Final = re.compile(r"#(\d+) \d+\.\d+(?: (.*))?")
_FAILED: Final = re.compile(r"#(\d+) ERROR: .*")
#: The legacy builder (no buildx) writes each step and its output to stdout instead.
_LEGACY_STEP: Final = re.compile(r"Step \d+/\d+ : ")
_LEGACY_INFO: Final = " ---> "  # its own lines between a step and the step's output
_EXIT: Final = re.compile(r"(?:exit code: |returned a non-zero code: )(\d+)")
#: Lines that never name the cause: a stack frame; npm's code, errno and syscall lines (a later
#: line says it in words), its notices and its log's path; Corepack's usage line.
_NOISE: Final = re.compile(
    r"at |npm (?:ERR!|error) (?:code|errno|syscall|A complete log)|npm notice|\$ "
)
#: A download that failed, which explains whatever fails after it: offline, ``apt-get update``
#: only warns, and ``apt-get install`` then fails with "Unable to locate package".
_NETWORK: Final = re.compile(
    r"Temporary failure resolving|Could not resolve (?:host|')|EAI_AGAIN|ENOTFOUND|ETIMEDOUT"
    r"|ECONNRESET|ECONNREFUSED|ENETUNREACH|EHOSTUNREACH|[Cc]onnection (?:timed out|refused|reset)"
    r"|Network is unreachable|i/o timeout|TLS handshake timeout"
    r"|Error when performing the request to"  # Corepack's, when the request itself fails
)
_ERROR: Final = re.compile(
    r"E: |npm (?:ERR!|error) |(?:Internal|Usage) Error: |(?:error|fatal|ERROR): "
    r"|[\w.]*(?:Error|Exception): "
)
#: What Docker's last line says before the error itself.
_SUMMARY: Final = re.compile(r"^(?:ERROR: *)?(?:failed to build: *)?(?:failed to solve: *)?")
_ESCAPE: Final = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_HEX: Final = re.compile(r"\b[0-9a-f]{32,}\b")  # digests and hashes: their first 12 digits
_TROUBLESHOOTING: Final = re.compile(r";? for troubleshooting help, see \S+")  # Corepack's


def image_tag(dockerfile: Path = DOCKERFILE, repository: str = "repro-sandbox") -> str:
    """``<repository>:<first 12 hex digits of the Dockerfile's SHA-256>``."""
    return f"{repository}:{hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:12]}"


def sandbox_image(docker: str = "docker") -> str:
    """The Python sandbox image's tag, built first if this machine does not have it."""
    return _built(DOCKERFILE, "repro-sandbox", "the sandbox image", docker)


def node_image(docker: str = "docker") -> str:
    """The Node.js sandbox image's tag, built first if this machine does not have it."""
    return _built(NODE_DOCKERFILE, "repro-sandbox-node", "the Node.js sandbox image", docker)


def build_failure(stderr: str, stdout: str = "", status: int = 1) -> str:
    """Why ``docker build`` failed, in one short line without the home directory.

    The failing step's output names the cause: a failed download first, else its first error
    line, else its last line, followed by the step's exit code. Docker's own last line is the
    fallback, for a step that printed nothing or a build that failed before any step ran (a
    base image that cannot be pulled, no daemon)."""
    progress = _lines(stderr)
    failed = next((m for m in map(_FAILED.fullmatch, progress) if m), None)
    if failed is not None:
        steps = map(_OUTPUT.fullmatch, progress)
        output = [m[2] or "" for m in steps if m and m[1] == failed[1]]
    else:
        output = _legacy_output(_lines(stdout))
    summary = next((line.strip() for line in reversed(progress) if line.strip()), "")
    cause = _cause(output)
    if cause is None:
        last = _SUMMARY.sub("", summary, count=1)
        return _short(last, ends=True) if last else f"docker build exited with status {status}"
    code = _EXIT.search(failed[0] if failed else summary)
    return _short(cause) + (f" (exit code {code[1]})" if code else "")


def _built(dockerfile: Path, repository: str, what: str, docker: str) -> str:
    tag = image_tag(dockerfile, repository)
    try:
        present = subprocess.run(  # noqa: S603 - docker, with Repro's own arguments
            [docker, "image", "inspect", "--format", "{{.Id}}", tag],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if present.returncode == 0:
            return tag
        # No retry: apt-get and npm retry their own downloads, and a second build would repeat
        # minutes of them inside a job whose deadline allows one build (docs/FAILURES.md).
        built = subprocess.run(  # noqa: S603 - docker, building Repro's own Dockerfile
            [docker, "build", "--tag", tag, "-"],
            input=dockerfile.read_bytes(),
            capture_output=True,
            timeout=BUILD_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError(f"{what}: {exc}") from None
    if built.returncode != 0:
        stderr, stdout = (s.decode("utf-8", "replace") for s in (built.stderr, built.stdout))
        raise SandboxError(
            f"building {what} failed: {build_failure(stderr, stdout, built.returncode)}"
        )
    return tag


def _lines(text: str) -> list[str]:
    return _ESCAPE.sub("", text.replace("\r\n", "\n").replace("\r", "\n")).split("\n")


def _legacy_output(lines: list[str]) -> list[str]:
    """The last step's output, from the legacy builder's stdout."""
    steps = [i for i, line in enumerate(lines) if _LEGACY_STEP.match(line)]
    after = lines[steps[-1] + 1 :] if steps else []
    return [line for line in after if not line.startswith(_LEGACY_INFO)]


def _cause(output: list[str]) -> str | None:
    """The line of a step's output that names why it failed, if any line is left."""
    lines = [line for line in (o.strip() for o in output) if line and not _NOISE.match(line)]
    network = [line for line in lines if _NETWORK.search(line)]
    if network:  # the one that names what it downloaded, if one does
        return next((line for line in network if "://" in line), network[0])
    named = [line for line in lines if _ERROR.match(line)]
    return named[0] if named else lines[-1] if lines else None


def _short(line: str, *, ends: bool = False) -> str:
    """``line`` at most ``CAUSE_MAX`` characters, cut at its end, or with ``ends`` in its
    middle: Docker's own errors are chains (``what: why: ...: the cause``)."""
    line = _TROUBLESHOOTING.sub("", without_home(line))
    line = " ".join(_HEX.sub(lambda m: m[0][:12] + "…", line).split())
    if len(line) <= CAUSE_MAX:
        return line
    if ends:
        return f"{line[:_HEAD]}…{line[_HEAD + 1 - CAUSE_MAX :]}"
    return line[: CAUSE_MAX - 1] + "…"
