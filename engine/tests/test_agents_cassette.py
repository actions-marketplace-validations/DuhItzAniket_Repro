"""Cassettes: record an agent call once, replay it offline in another run (G02).

A stand-in reviewer reads the evidence its prompt names through a Repro tool and cites it.
Each "run" draws its own evidence IDs, as real runs do, so a replay in a second run is only
right if the recording's IDs become that run's.
"""

import asyncio
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, query
from conftest import OFFLINE_API, OFFLINE_KEY, Golden

from fake_messages_api import FakeMessagesApi, text, tool_use
from refusing_proxy import RefusingProxy
from repro.agents import (
    AgentRequest,
    AgentResult,
    AgentSdkBackend,
    Budget,
    CassetteBackend,
    MissingCassetteError,
    Progress,
    ReproTool,
    StaleCassetteError,
    Usage,
)
from repro.agents.cassette import RUN_SPECIFIC_ID, CassetteKey, Ids, input_digest
from repro.agents.lockdown import STRUCTURED_OUTPUT
from repro.ids import new_evidence_id, new_run_id

SCHEMA_PROPERTIES = {"verdict": {"type": "string"}, "cited": {"type": "array"}}


class Run:
    """One run's evidence: two items, the first pointing at the second."""

    def __init__(
        self,
        claim_text: str = "rounds half up",
        related_text: str = "0.5 -> 1",
        *,
        ids: tuple[str, str, str] | None = None,
    ) -> None:
        self.run_id, self.claim, self.related = ids or (
            new_run_id(),
            new_evidence_id(),
            new_evidence_id(),
        )
        self.texts = {self.claim: claim_text, self.related: related_text}

    async def get_evidence(self, arguments: Mapping[str, Any]) -> str:
        ident = str(arguments["id"])
        if ident not in self.texts:
            raise KeyError(f"no evidence {ident}")
        see = f"; see {self.related}" if ident == self.claim else ""
        return f"{ident}: {self.texts[ident]}{see}"

    def request(self, *, prompt: str | None = None, **changes: Any) -> AgentRequest:
        schema = {
            "type": "object",
            "properties": {
                **SCHEMA_PROPERTIES,
                "cited": {"type": "array", "items": {"enum": [self.claim, self.related]}},
            },
        }
        fields: dict[str, Any] = {
            "role": "review",
            "system_prompt": "Judge the claim against the evidence.",
            "prompt": prompt or f"Run {self.run_id}. Claim C1 rests on {self.claim}.",
            "model": "claude-opus-5-5",
            "prompt_version": "review/1",
            "budget": Budget(max_turns=4),
            "tools": (
                ReproTool(
                    "get_evidence",
                    "Reads one evidence item.",
                    {"type": "object", "properties": {"id": {"type": "string"}}},
                    self.get_evidence,
                ),
            ),
            "output_schema": schema,
        }
        return AgentRequest(**(fields | changes))


class Reviewer:
    """A stand-in model: reads the evidence its prompt names, follows it, cites both."""

    def __init__(self, outcome: str = "ok", made_up: str | None = None) -> None:
        self.outcome = outcome
        self.made_up = made_up
        self.calls = 0

    async def run(self, request: AgentRequest, on_progress: Any = None) -> AgentResult:
        self.calls += 1
        say = on_progress or (lambda update: None)
        claim = next(
            m.group(0) for m in RUN_SPECIFIC_ID.finditer(request.prompt) if m.group(1) == "ev"
        )
        say(Progress("thinking", 1, detail=f"The claim rests on {claim}."))
        say(Progress("tool_call", 1, "mcp__repro__get_evidence", f"get_evidence({claim})"))
        read = await request.tools[0].handler({"id": claim})
        related = read.rsplit("see ", 1)[1]
        say(Progress("tool_call", 2, "mcp__repro__get_evidence", f"get_evidence({related})"))
        await request.tools[0].handler({"id": related})
        cited = [claim, related] + ([self.made_up] if self.made_up else [])
        return AgentResult(
            self.outcome,  # type: ignore[arg-type]
            structured={"verdict": "supported", "cited": cited} if self.outcome == "ok" else None,
            text=f"Supported by {claim} and {related}.",
            turns=3,
            usage=Usage(1200, 300, 800, 0.0123),
            detail="" if self.outcome == "ok" else "the API went away",
            tool_calls=(("mcp__repro__get_evidence", True), ("mcp__repro__get_evidence", True)),
            by_model={"claude-opus-5-5": Usage(1200, 300, 800, 0.0123)},
        )


def record(root: Path, run: Run, reviewer: Reviewer | None = None, **changes: Any) -> AgentResult:
    backend = CassetteBackend(root, "record", reviewer or Reviewer())
    return asyncio.run(backend.run(run.request(**changes)))


def replay(
    root: Path, run: Run, progress: list[Progress] | None = None, **changes: Any
) -> AgentResult:
    backend = CassetteBackend(root, "replay")
    seen = progress if progress is not None else []
    return asyncio.run(backend.run(run.request(**changes), seen.append))


def cassette_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.json"))


# -- the key ----------------------------------------------------------------------------------


def test_ids_become_their_kind_and_first_seen_position() -> None:
    ids = Ids()
    a, b, r = new_evidence_id(), new_evidence_id(), new_run_id()
    assert ids.normalise(f"{b} {r} {a} {b}") == "ev_#1 run_#2 ev_#3 ev_#1"
    assert ids.seen == [b, r, a]
    assert ids.normalise(f"{a} then ev_123 and C1") == "ev_#3 then ev_123 and C1"


def test_the_same_call_in_another_run_has_the_same_digest() -> None:
    first, second = Run(), Run()
    assert first.claim != second.claim
    assert input_digest(first.request()) == input_digest(second.request())
    assert CassetteKey.of(first.request()) == CassetteKey.of(second.request())


def test_an_id_at_the_start_of_a_line_is_normalised_too() -> None:
    """G08: the QA agent lists evidence one per line, each line starting with its ID. In the
    input's JSON the ID follows the ``\\n`` escape, where a word boundary does not match, so
    the IDs were left as they were and every run made another call."""
    runs = [Run(), Run()]
    digests = []
    for run in runs:
        listing = f"The evidence:\n{run.claim}  behaviour_diff\n{run.related}  test_outcome"
        ids = Ids()
        digests.append(input_digest(run.request(prompt=listing), ids))
        assert ids.seen == [run.claim, run.related]
    assert digests[0] == digests[1]


@pytest.mark.parametrize(
    "change",
    [
        {"system_prompt": "Judge it."},
        {"prompt": "Claim C2 rests on nothing."},
        {"effort": "high"},
        {"output_schema": None},
        {"tools": ()},
        {"role": "gap"},
    ],
)
def test_anything_the_model_is_given_changes_the_digest(change: dict[str, Any]) -> None:
    run = Run()
    assert input_digest(run.request(**change)) != input_digest(run.request())


def test_the_budget_does_not_change_the_digest() -> None:
    """A run's ledger lowers a call's dollar budget: the call is the same call."""
    run = Run()
    lowered = run.request(budget=Budget(max_turns=4, max_budget_usd=0.01, timeout_s=5))
    assert input_digest(lowered) == input_digest(run.request())


def test_a_cassettes_path_is_its_key() -> None:
    key = CassetteKey("review/3", "us.anthropic.claude:1", "ab" * 32)
    path = key.path(Path("cassettes"))
    assert path.parts[:4] == ("cassettes", "review", "3", "us.anthropic.claude_1")
    assert path.name == "ab" * 32 + ".json"


def test_replay_is_given_no_backend_and_recording_needs_one(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="never makes a call"):
        CassetteBackend(tmp_path, "replay", Reviewer())
    with pytest.raises(ValueError, match="needs a backend"):
        CassetteBackend(tmp_path, "record")


# -- record, then replay in another run -------------------------------------------------------


def test_a_recorded_call_replays_in_another_run_with_that_runs_ids(tmp_path: Path) -> None:
    first, second = Run(), Run()
    reviewer = Reviewer()
    live = record(tmp_path, first, reviewer)
    progress: list[Progress] = []
    again = replay(tmp_path, second, progress)

    assert reviewer.calls == 1  # replay made no call
    assert live.structured == {"verdict": "supported", "cited": [first.claim, first.related]}
    assert again.structured == {"verdict": "supported", "cited": [second.claim, second.related]}
    assert again.text == f"Supported by {second.claim} and {second.related}."
    assert [p.detail for p in progress] == [
        f"The claim rests on {second.claim}.",
        f"get_evidence({second.claim})",
        f"get_evidence({second.related})",
    ]
    assert (again.outcome, again.turns, again.tool_calls) == ("ok", 3, live.tool_calls)
    assert (again.usage, dict(again.by_model)) == (live.usage, dict(live.by_model))
    assert again.replayed
    assert not live.replayed


def test_an_id_the_model_made_up_is_left_as_it_is(tmp_path: Path) -> None:
    made_up = new_evidence_id()
    record(tmp_path, Run(), Reviewer(made_up=made_up))
    second = Run()
    again = replay(tmp_path, second)
    assert again.structured["cited"] == [second.claim, second.related, made_up]


def test_a_missing_cassette_fails_and_calls_nothing(tmp_path: Path) -> None:
    run = Run()
    with pytest.raises(MissingCassetteError, match=r"review/1 on claude-opus-5-5") as failure:
        replay(tmp_path, run)
    assert str(CassetteKey.of(run.request()).path(tmp_path)) in str(failure.value)


def test_a_changed_prompt_is_a_different_call(tmp_path: Path) -> None:
    record(tmp_path, Run())
    with pytest.raises(MissingCassetteError):
        replay(tmp_path, Run(), prompt="Claim C1 is about rounding.")


def test_evidence_that_changed_since_the_recording_makes_it_stale(tmp_path: Path) -> None:
    record(tmp_path, Run())
    with pytest.raises(StaleCassetteError, match="get_evidence"):
        replay(tmp_path, Run(related_text="0.5 -> 0"))


def test_a_tool_that_now_fails_makes_it_stale(tmp_path: Path) -> None:
    record(tmp_path, Run())
    broken = Run()
    broken.texts.pop(broken.related)
    with pytest.raises(StaleCassetteError):
        replay(tmp_path, broken)


@pytest.mark.parametrize("outcome", ["timeout", "error"])
def test_infrastructure_failures_are_not_recorded(tmp_path: Path, outcome: str) -> None:
    result = record(tmp_path, Run(), Reviewer(outcome=outcome))
    assert result.outcome == outcome
    assert cassette_files(tmp_path) == []


@pytest.mark.parametrize("outcome", ["max_turns", "max_budget", "invalid_output"])
def test_what_the_model_did_is_recorded_however_it_ended(tmp_path: Path, outcome: str) -> None:
    record(tmp_path, Run(), Reviewer(outcome=outcome))
    assert replay(tmp_path, Run()).outcome == outcome


# -- the file -------------------------------------------------------------------------------


def test_a_cassette_is_canonical_and_changes_only_with_the_call(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    run = Run()
    record(first, run)
    record(second, run)
    [one], [two] = cassette_files(first), cassette_files(second)
    data = one.read_bytes()
    assert data == two.read_bytes()  # no recording time
    assert b"\r" not in data
    assert data.endswith(b"}\n")
    document = json.loads(data)
    assert (
        data.decode() == json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    )
    assert set(document) == {
        "cassette",
        "key",
        "request",
        "ids",
        "transcript",
        "result",
        "recorded_with",
    }
    # First seen in the input's canonical order: the output schema's enum, then the prompt.
    assert document["ids"] == [run.claim, run.related, run.run_id]
    assert [next(iter(entry)) for entry in document["transcript"]] == [
        "progress",
        "progress",
        "tool",
        "progress",
        "tool",
    ]


def test_a_cassette_is_scrubbed_of_credentials(tmp_path: Path) -> None:
    token = "ghp_" + "A1b2C3d4" * 5  # built here, so no scanner mistakes the test for a leak
    run = Run()
    record(tmp_path, run, prompt=f"Claim C1 rests on {run.claim}. {token}")
    [path] = cassette_files(tmp_path)
    assert token not in path.read_text(encoding="utf-8")
    assert "[redacted]" in path.read_text(encoding="utf-8")


def test_a_cassette_does_not_carry_the_home_directory(tmp_path: Path) -> None:
    run = Run()
    record(tmp_path / "c", run, prompt=f"Claim C1 rests on {run.claim}. Seen in {Path.home()}.")
    [path] = cassette_files(tmp_path / "c")
    text = path.read_text(encoding="utf-8")
    assert str(Path.home()) not in text
    assert json.dumps(str(Path.home()))[1:-1] not in text  # nor as JSON escapes it
    assert "Seen in ~." in text


def test_no_test_can_reach_a_real_model() -> None:
    """The repository's conftest points every test at a closed loopback port with a dummy
    key, whatever the machine's own environment holds."""
    assert os.environ["ANTHROPIC_BASE_URL"] == OFFLINE_API
    assert os.environ["ANTHROPIC_API_KEY"] == OFFLINE_KEY
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ


# -- through the real CLI -------------------------------------------------------------------

#: Fixed IDs for the recording, so its cassette can be a golden file.
RECORDED_IDS = (
    "run_01JBAAHWY00000000000000001",
    "ev_01JBAAHWY00000000000000002",
    "ev_01JBAAHWY00000000000000003",
)


def model_turns(body: Mapping[str, Any]) -> int:
    messages: list[Mapping[str, Any]] = body["messages"]
    return sum(1 for message in messages if message["role"] == "assistant")


@pytest.mark.model_cli
def test_a_call_recorded_through_the_cli_replays_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, golden: Golden
) -> None:
    """Recorded once against a stand-in for the API, through the bundled CLI and the lockdown;
    then replayed in another run with no API at all (the offline guard's closed port)."""
    first = Run(ids=RECORDED_IDS)

    def script(body: Mapping[str, Any]) -> list[dict[str, Any]]:
        answer = {"verdict": "supported", "cited": [first.claim]}
        if model_turns(body) == 0:
            return [tool_use("mcp__repro__get_evidence", {"id": first.claim}, call_id="toolu_read")]
        return [tool_use(STRUCTURED_OUTPUT, answer, call_id="toolu_answer")]  # ends the session

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    api = FakeMessagesApi(script)
    with api.serving():
        monkeypatch.setenv("ANTHROPIC_BASE_URL", api.url)
        live = AgentSdkBackend(tmp_path / "scratch")
        recorder = CassetteBackend(tmp_path / "cassettes", "record", live)
        recorded = asyncio.run(recorder.run(first.request()))
    monkeypatch.setenv("ANTHROPIC_BASE_URL", OFFLINE_API)

    assert recorded.outcome == "ok", recorded.detail
    assert len(api.messages()) == 2
    [path] = cassette_files(tmp_path / "cassettes")
    golden.check("cassettes/review-1.json", path.read_text(encoding="utf-8"))

    second = Run()
    again = replay(tmp_path / "cassettes", second)
    assert again.structured == {"verdict": "supported", "cited": [second.claim]}
    assert (again.usage, again.tool_calls) == (recorded.usage, recorded.tool_calls)
    assert again.replayed
    assert len(api.messages()) == 2  # nothing more reached the stand-in


@pytest.mark.model_cli
def test_a_call_asked_for_its_answer_records_and_replays_like_any_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A call whose lookups used every turn, then answered in the turn kept for it: the
    cassette is the same kind as any other's, the step that asked for the answer is in its
    transcript, and a replay in another run gives the same answer, turns and usage."""
    first = Run()

    def script(body: Mapping[str, Any]) -> list[dict[str, Any]]:
        if {tool["name"] for tool in body.get("tools", [])} == {STRUCTURED_OUTPUT}:
            answer = {"verdict": "supported", "cited": [first.claim]}
            return [tool_use(STRUCTURED_OUTPUT, answer, call_id="toolu_answer")]
        read = {"id": first.claim}
        return [tool_use("mcp__repro__get_evidence", read, call_id=f"toolu_{model_turns(body)}")]

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    api = FakeMessagesApi(script)
    with api.serving():
        monkeypatch.setenv("ANTHROPIC_BASE_URL", api.url)
        live = AgentSdkBackend(tmp_path / "scratch")
        recorder = CassetteBackend(tmp_path / "cassettes", "record", live)
        recorded = asyncio.run(recorder.run(first.request(budget=Budget(max_turns=2))))
    monkeypatch.setenv("ANTHROPIC_BASE_URL", OFFLINE_API)

    assert recorded.outcome == "ok", recorded.detail
    assert (recorded.turns, len(api.messages())) == (3, 3)
    [path] = cassette_files(tmp_path / "cassettes")
    document = json.loads(path.read_text(encoding="utf-8"))
    steps = [entry["progress"] for entry in document["transcript"] if "progress" in entry]
    assert [(step["turn"], step["tool"]) for step in steps] == [
        (1, "mcp__repro__get_evidence"),
        (2, "mcp__repro__get_evidence"),
        (3, None),  # the step that asked for the answer
        (3, STRUCTURED_OUTPUT),
    ]

    second = Run()
    progress: list[Progress] = []
    again = replay(tmp_path / "cassettes", second, progress, budget=Budget(max_turns=2))
    assert again.structured == {"verdict": "supported", "cited": [second.claim]}
    assert (again.turns, again.usage, again.tool_calls) == (
        recorded.turns,
        recorded.usage,
        recorded.tool_calls,
    )
    assert progress[2].detail == "no answer after 2 turns of lookups: asked for it now"
    assert len(api.messages()) == 3


def test_control_the_offline_check_sees_the_cli_leave_the_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The proxy the CLI tests run behind does catch the CLI's own traffic: with its
    nonessential traffic left on, an unlocked CLI tries to reach Anthropic's API directly
    (feature flags, telemetry) even when the model API is local. Refused, so still offline."""
    with (
        RefusingProxy().serving() as proxy,
        FakeMessagesApi(lambda body: [text("ok")]).serving() as api,
    ):
        monkeypatch.delenv("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", api.url)
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            monkeypatch.setenv(name, proxy.url)
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
        (tmp_path / "config").mkdir()
        options = ClaudeAgentOptions(
            tools=[],
            setting_sources=[],
            permission_mode="dontAsk",
            cwd=str(tmp_path),
            env={"CLAUDE_CONFIG_DIR": str(tmp_path / "config")},
            model="claude-sonnet-5-5",
            max_turns=1,
        )

        async def drain() -> None:
            async for _ in query(prompt="hi", options=options):
                pass

        asyncio.run(drain())

    assert len(api.messages()) == 1
    assert proxy.attempts
    assert {attempt.split(":")[0] for attempt in proxy.attempts} == {"api.anthropic.com"}


def test_recording_again_calls_only_for_what_has_no_cassette(tmp_path: Path) -> None:
    reviewer = Reviewer()
    record(tmp_path, Run(), reviewer)
    again = record(tmp_path, Run(), reviewer)  # the same call in another run
    assert reviewer.calls == 1
    assert again.replayed
    record(tmp_path, Run(related_text="0.5 -> 0"), reviewer)  # stale: recorded again
    assert reviewer.calls == 2
