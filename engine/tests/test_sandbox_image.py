"""A sandbox image that does not build says why (D03, K11).

``docker build`` ends a failed build with one line that repeats the failing step's whole command
and gives only its exit code. The cause is in the step's own output, printed before it. That
output is what the error, and so the card's ``environment_failed`` detail, reports.

Each sample in ``data/docker_build`` is ``docker build``'s stderr (and, for the legacy builder,
its stdout) exactly as captured on 2026-10-25 with Docker Engine 29.8.2 and buildx 0.38.1 on
Ubuntu in WSL2. The builds ran as ``repro.sandbox.image`` runs them, with the Dockerfile on stdin
and the output piped, plus ``--no-cache``. Each one starts from ``node.Dockerfile``'s base image
and fails in one way; ``--network none`` stands in for a network that fails.
"""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from repro.readable import reason
from repro.sandbox import SandboxError
from repro.sandbox.image import (
    CAUSE_MAX,
    NODE_DOCKERFILE,
    build_failure,
    image_tag,
    node_image,
)

SAMPLES = Path(__file__).parent / "data" / "docker_build"
OFFLINE_APT = (
    "W: Failed to fetch http://deb.debian.org/debian/dists/bookworm/InRelease Temporary failure "
    "resolving 'deb.debian.org' (exit code 100)"
)
CAUSES = {
    # apt-get install of a package Debian does not have.
    "apt": "E: Unable to locate package git-does-not-exist (exit code 100)",
    # Offline, apt-get update only warns; install then fails with "Unable to locate package".
    "apt-offline": OFFLINE_APT,
    # Repro's own node.Dockerfile, offline: its apt-get update is the first download.
    "node-offline": OFFLINE_APT,
    # corepack install with a pinned hash the download does not have.
    "corepack-hash": "Internal Error: Mismatch hashes. Expected 000000000000…, got ba98abc58553… "
    "(exit code 1)",
    # corepack install of a version that does not exist.
    "corepack-usage": "Usage Error: Tag not found (not-a-version) (exit code 1)",
    # corepack install, offline: its error line comes before the stack, past BuildKit's last 10.
    "corepack-offline": "Internal Error: Error when performing the request to "
    "https://registry.npmjs.org/pnpm/-/pnpm-10.34.7.tgz (exit code 1)",
    # npm install of a package the registry does not have.
    "npm-404": "npm error 404 Not Found - GET "
    "https://registry.npmjs.org/repro-capture-no-such-package-0d1f - Not found (exit code 1)",
    # npm install, offline.
    "npm-offline": "npm error request to https://registry.npmjs.org/left-pad failed, reason: "
    "getaddrinfo EAI_AGAIN registry.npmjs.org (exit code 1)",
    # A command that fails with no error line of its own: its last line.
    "unknown": "step two (exit code 3)",
    # The legacy builder (DOCKER_BUILDKIT=0), whose steps print to stdout, in colour.
    "legacy": "E: Unable to locate package git-does-not-exist (exit code 100)",
}
#: Builds whose failing step printed nothing, or that failed before any step ran: Docker's last
#: line, without its "ERROR: failed to build: failed to solve:".
SUMMARIES = {
    "silent": 'process "/bin/sh -c false" did not complete successfully: exit code: 1',
    "no-daemon": "Cannot connect to the Docker daemon at tcp://127.0.0.1:9. Is the docker daemon "
    "running?",
    # A base image that cannot be pulled: the chain keeps the image and, at its end, the cause.
    "pull-offline": "registry.invalid/repro/no-such-image:1: failed to resolve so…t: Head "
    '"https://registry.invalid/v2/repro/no-such-image/manifests/1": dial tcp: lookup '
    "registry.invalid on 10.255.255.254:53: no such host",
    "base-missing": "repro-capture/no-such-image:1: failed to resolve source meta…age:1: pull "
    "access denied, repository does not exist or may require authorization: server message: "
    "insufficient_scope: authorization failed",
}


def sample(name: str) -> tuple[str, str]:
    """A sample's stderr and stdout, decoded as ``repro.sandbox.image`` decodes them."""
    out = SAMPLES / f"{name}.stdout"
    stdout = out.read_bytes().decode("utf-8", "replace") if out.exists() else ""
    return (SAMPLES / f"{name}.stderr").read_bytes().decode("utf-8", "replace"), stdout


@pytest.mark.parametrize(("name", "cause"), CAUSES.items())
def test_a_step_that_fails_is_reported_by_its_own_output(name: str, cause: str) -> None:
    assert build_failure(*sample(name)) == cause


@pytest.mark.parametrize(("name", "summary"), SUMMARIES.items())
def test_a_build_with_no_step_output_falls_back_to_dockers_last_line(
    name: str, summary: str
) -> None:
    assert build_failure(*sample(name)) == summary


def test_every_sample_is_covered() -> None:
    names = {path.stem for path in SAMPLES.glob("*.stderr")}
    assert names == set(CAUSES) | set(SUMMARIES)


class FakeDocker:
    """``subprocess.run`` for ``docker``: an image it has (or not), and a build's outcome."""

    def __init__(self, *, present: bool, build: tuple[int, str, str] = (0, "", "")) -> None:
        self.present, self.build = present, build
        self.commands: list[str] = []

    def __call__(self, argv: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(argv[1])
        if argv[1] == "image":
            return subprocess.CompletedProcess(argv, 0 if self.present else 1, b"", b"")
        status, stdout, stderr = self.build
        return subprocess.CompletedProcess(argv, status, stdout.encode(), stderr.encode())


@pytest.mark.parametrize("name", [*CAUSES, *SUMMARIES])
def test_the_cards_detail_names_the_cause_whole(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    stderr, stdout = sample(name)
    monkeypatch.setattr(subprocess, "run", FakeDocker(present=False, build=(1, stdout, stderr)))
    with pytest.raises(SandboxError) as raised:
        node_image()
    cause = CAUSES.get(name) or SUMMARIES[name]
    assert str(raised.value) == f"building the Node.js sandbox image failed: {cause}"
    assert reason(raised.value).endswith(cause)  # within the card's limit, not cut short
    if name in CAUSES:
        assert "did not complete successfully" not in str(raised.value)


def test_an_image_this_machine_has_is_not_built(monkeypatch: pytest.MonkeyPatch) -> None:
    docker = FakeDocker(present=True)
    monkeypatch.setattr(subprocess, "run", docker)
    assert node_image() == image_tag(NODE_DOCKERFILE, "repro-sandbox-node")
    assert docker.commands == ["image"]


def test_only_the_failing_steps_output_is_read() -> None:
    stderr = "\n".join([
        "#6 [stage-b 2/2] RUN make",
        "#5 [stage-a 2/2] RUN ./configure",
        "#6 0.101 error: a line of the step that did not fail",
        "#5 0.102 checking for gcc... yes",
        "#5 0.205 configure: error: no acceptable C compiler found in $PATH",
        "#6 CANCELED",
        '#5 ERROR: process "/bin/sh -c ./configure" did not complete successfully: exit code: 77',
        'ERROR: failed to build: failed to solve: process "/bin/sh -c ./configure" did not '
        "complete successfully: exit code: 77",
    ])  # fmt: skip
    assert build_failure(stderr) == (
        "configure: error: no acceptable C compiler found in $PATH (exit code 77)"
    )


def test_the_home_directory_is_written_as_a_tilde() -> None:
    home = str(Path.home())
    stderr = f"ERROR: failed to build: open {home}/Dockerfile: permission denied\n"
    found = build_failure(stderr)
    assert found == "open ~/Dockerfile: permission denied"
    assert home not in found


def test_a_long_line_is_cut_at_its_end() -> None:
    failed = '#5 ERROR: process "x" did not complete successfully: exit code: 100'
    found = build_failure(f"#5 0.1 E: {'x' * 300}\n{failed}\n")
    assert found == f"E: {'x' * (CAUSE_MAX - 4)}… (exit code 100)"


def test_a_build_that_says_nothing_gives_its_exit_status() -> None:
    assert build_failure("", "", 125) == "docker build exited with status 125"
