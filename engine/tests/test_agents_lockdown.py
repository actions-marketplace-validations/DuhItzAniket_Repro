"""The agent lockdown's options and tool gate, and the backend's request types (G01; ADR-0007).

These check what ``AgentSdkBackend`` asks of the SDK; ``test_agents_cli`` checks what the real
Claude Code CLI then does with it.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import ANY

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    HookCallback,
    HookContext,
    HookJSONOutput,
    Message,
    PreToolUseHookInput,
    ResultMessage,
    ToolUseBlock,
)

from repro.agents import AgentRequest, AgentResult, AgentSdkBackend, Budget, Progress, ReproTool
from repro.agents.lockdown import (
    STRUCTURED_OUTPUT,
    lockdown_env,
    lockdown_options,
    permitted_tools,
    tool_gate,
)
from repro.agents.sdk import ANSWER_NOW, ANSWER_TURNS


async def answer(arguments: Any) -> str:
    return "ok"


PING = ReproTool("ping", "Answers.", {"type": "object", "properties": {}}, answer)
EVIDENCE = ReproTool("read_evidence", "Reads.", {"type": "object", "properties": {}}, answer)
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


def request(*, tools: tuple[ReproTool, ...] = (PING,), schema: Any = None) -> AgentRequest:
    return AgentRequest(
        role="review",
        system_prompt="Judge the claim.",
        prompt="<untrusted_pr_description>x</untrusted_pr_description>",
        model="claude-opus-5-5",
        prompt_version="review/1",
        budget=Budget(max_turns=5, max_budget_usd=0.5, timeout_s=60),
        tools=tools,
        output_schema=schema,
        effort="high",
    )


def decide(permitted: frozenset[str], tool_name: str) -> dict[str, Any]:
    hook_input = PreToolUseHookInput(
        session_id="s",
        transcript_path="t",
        cwd="c",
        hook_event_name="PreToolUse",
        tool_name=tool_name,
        tool_input={},
        tool_use_id="toolu_1",
    )
    context: HookContext = {"signal": None}

    async def ask() -> HookJSONOutput:
        return await tool_gate(permitted)(hook_input, "toolu_1", context)

    return dict(asyncio.run(ask()))


def test_the_options_close_every_way_in(tmp_path: Path) -> None:
    options = lockdown_options(
        request(),
        cwd=tmp_path / "cwd",
        config_dir=tmp_path / "config",
        system_prompt_file=tmp_path / "system.md",
        server=None,
    )
    assert options.tools == []  # no built-in tool in context
    assert options.allowed_tools == ["mcp__repro__ping"]
    assert options.disallowed_tools == []
    assert options.permission_mode == "dontAsk"
    assert options.can_use_tool is None  # dontAsk never asks
    assert options.setting_sources == []  # an empty list: None would load settings
    assert options.strict_mcp_config is True
    assert options.plugins == []
    assert options.skills is None
    assert options.verbatim_prompts is True
    assert options.add_dirs == []
    assert options.cwd == str(tmp_path / "cwd")
    assert options.system_prompt == {"type": "file", "path": str(tmp_path / "system.md")}
    assert options.env == lockdown_env(tmp_path / "config")
    assert (options.model, options.effort) == ("claude-opus-5-5", "high")
    assert (options.max_turns, options.max_budget_usd) == (5, 0.5)
    assert options.output_format is None
    assert options.mcp_servers == {}
    hooks = options.hooks or {}
    assert list(hooks) == ["PreToolUse"]
    assert [matcher.matcher for matcher in hooks["PreToolUse"]] == [None]  # every tool


def test_an_output_schema_permits_structured_output_and_nothing_more(tmp_path: Path) -> None:
    options = lockdown_options(
        request(schema=SCHEMA),
        cwd=tmp_path,
        config_dir=tmp_path,
        system_prompt_file=tmp_path / "system.md",
        server=None,
    )
    assert options.output_format == {"type": "json_schema", "schema": SCHEMA}
    assert options.allowed_tools == [STRUCTURED_OUTPUT, "mcp__repro__ping"]


def test_the_environment_disables_memory_settings_and_subagents(tmp_path: Path) -> None:
    env = lockdown_env(tmp_path)
    assert env == {
        "CLAUDE_CONFIG_DIR": str(tmp_path),
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1",
        "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "1",
    }
    assert not any(key.startswith("ANTHROPIC_") for key in env)  # credentials are inherited


def test_a_call_may_run_its_own_tools_only() -> None:
    assert permitted_tools(request()) == {"mcp__repro__ping"}
    assert permitted_tools(request(tools=(PING, EVIDENCE), schema=SCHEMA)) == {
        "mcp__repro__ping",
        "mcp__repro__read_evidence",
        STRUCTURED_OUTPUT,
    }
    assert permitted_tools(request(tools=())) == frozenset()


@pytest.mark.parametrize(
    "tool_name",
    [
        "Bash",
        "Read",
        "Write",
        "WebFetch",
        "Agent",
        "Skill",
        "mcp__other__ping",
        "mcp__repro__read_evidence",  # a Repro tool, but not this call's
        "mcp__repro__",
        "mcp__repro__ping__x",
        "MCP__REPRO__PING",
        STRUCTURED_OUTPUT,  # no output schema was asked for
        "",
    ],
)
def test_the_gate_denies_every_other_tool(tool_name: str) -> None:
    decision = decide(permitted_tools(request()), tool_name)
    specific = decision["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "deny"


@pytest.mark.parametrize("tool_name", ["mcp__repro__ping", STRUCTURED_OUTPUT])
def test_the_gate_leaves_the_calls_own_tools_to_the_allow_rules(tool_name: str) -> None:
    assert decide(permitted_tools(request(schema=SCHEMA)), tool_name) == {}


@pytest.mark.parametrize(
    ("turns", "usd", "seconds"), [(0, 1.0, 1.0), (1, 0.0, 1.0), (1, 1.0, 0.0), (1, -1.0, 1.0)]
)
def test_every_budget_limit_is_positive(turns: int, usd: float, seconds: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        Budget(max_turns=turns, max_budget_usd=usd, timeout_s=seconds)


@pytest.mark.parametrize(
    "name", ["Ping", "read-evidence", "read evidence", "", "_", "mcp__x", "x_", "9lives", "a" * 49]
)
def test_tool_names_are_lower_snake_case(name: str) -> None:
    with pytest.raises(ValueError, match="lower_snake_case"):
        ReproTool(name, "x", {"type": "object"}, answer)


# -- the answer's turns, after lookups that used every turn -----------------------------------
#
# ``test_agents_cli`` runs them through the real CLI; here a stand-in for the SDK's ``query``
# shows what the backend asks of it, and what it reports, in each case.


def responded(number: int, *blocks: Any) -> AssistantMessage:
    return AssistantMessage(
        content=list(blocks), model="claude-opus-5-5", message_id=f"msg_{number}"
    )


def ended(
    subtype: str, *, cost: float, tokens: int, structured: Any = None, session: str = "s-1"
) -> ResultMessage:
    """A session's result; ``model_usage`` counts every session of the call, as a resumed
    session's does (observed with CLI 2.1.300)."""
    usage: Any = {"claude-opus-5-5": {"inputTokens": tokens, "outputTokens": 10, "costUSD": cost}}
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=subtype != "success",
        num_turns=99,  # the CLI's own count, which the backend does not report
        session_id=session,
        total_cost_usd=cost,
        model_usage=usage,
        structured_output=structured,
    )


LOOKUP = ToolUseBlock(id="toolu_1", name="mcp__repro__ping", input={})
OUT_OF_TURNS: list[Message] = [
    responded(1, LOOKUP),
    responded(2, LOOKUP),
    ended("error_max_turns", cost=0.2, tokens=300),
]
ANSWERED: list[Message] = [
    responded(3, ToolUseBlock(id="toolu_2", name=STRUCTURED_OUTPUT, input={"answer": "yes"})),
    ended("success", cost=0.25, tokens=450, structured={"answer": "yes"}),
]


class FakeSdk:
    """Stands in for ``query``: each session yields its messages (None: it never ends) and,
    after an error result, raises as the SDK does; each session's prompt and options are kept."""

    def __init__(self, *sessions: list[Message] | None) -> None:
        self.sessions = list(sessions)
        self.calls: list[tuple[str, ClaudeAgentOptions]] = []

    def query(self, *, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        self.calls.append((prompt, options))
        messages = self.sessions[len(self.calls) - 1]

        async def stream() -> AsyncIterator[Message]:
            if messages is None:
                await asyncio.sleep(3600)
                return
            for message in messages:
                yield message
            last = messages[-1] if messages else None
            if isinstance(last, ResultMessage) and last.is_error:
                raise ClaudeSDKError("Claude Code returned an error result")

        return stream()


def run_faked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake: FakeSdk,
    call: AgentRequest,
    progress: list[Progress] | None = None,
) -> AgentResult:
    monkeypatch.setattr("repro.agents.sdk.query", fake.query)
    shown = progress if progress is not None else []
    return asyncio.run(AgentSdkBackend(tmp_path).run(call, shown.append))


def gate_of(options: ClaudeAgentOptions) -> HookCallback:
    [matcher] = (options.hooks or {})["PreToolUse"]
    return matcher.hooks[0]


def test_a_call_out_of_turns_is_resumed_for_its_answer_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeSdk(OUT_OF_TURNS, ANSWERED)
    progress: list[Progress] = []
    result = run_faked(monkeypatch, tmp_path, fake, request(schema=SCHEMA), progress)

    assert result.outcome == "ok", result.detail
    assert result.structured == {"answer": "yes"}
    assert result.turns == 3  # two lookups and the answer: the model's responses
    assert result.tool_calls == (("mcp__repro__ping", True),) * 2 + ((STRUCTURED_OUTPUT, True),)
    assert (result.usage.input_tokens, result.usage.cost_usd) == (450, 0.25)  # the whole call's
    note = Progress("thinking", 3, detail="no answer after 2 turns of lookups: asked for it now")
    assert progress[2:4] == [note, Progress("tool_call", 3, STRUCTURED_OUTPUT, ANY)]

    (prompt, lookups), (asked, answering) = fake.calls
    assert prompt == request().prompt
    assert asked == ANSWER_NOW
    assert answering.resume == "s-1"
    assert lookups.resume is None
    # The same lockdown, the same workspace and system prompt; no Repro tool, ANSWER_TURNS
    # turns, and what the lookups left of the dollar budget.
    assert answering.tools == []
    assert answering.mcp_servers == {}
    assert answering.allowed_tools == [STRUCTURED_OUTPUT]
    assert answering.max_turns == ANSWER_TURNS
    assert answering.max_budget_usd == pytest.approx(0.3)
    for same in ("cwd", "env", "system_prompt", "model", "effort", "output_format"):
        assert getattr(answering, same) == getattr(lookups, same), same
    for same in ("permission_mode", "setting_sources", "strict_mcp_config", "verbatim_prompts"):
        assert getattr(answering, same) == getattr(lookups, same), same
    hook_input = PreToolUseHookInput(
        session_id="s",
        transcript_path="t",
        cwd="c",
        hook_event_name="PreToolUse",
        tool_name="mcp__repro__ping",
        tool_input={},
        tool_use_id="toolu_1",
    )
    context: HookContext = {"signal": None}

    async def ask() -> HookJSONOutput:
        return await gate_of(answering)(hook_input, "toolu_1", context)

    denied: dict[str, Any] = dict(asyncio.run(ask()))
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    ("schema", "lookups", "outcome"),
    [
        (None, OUT_OF_TURNS, "max_turns"),  # no structured answer to ask for
        (
            SCHEMA,
            [*OUT_OF_TURNS[:2], ended("error_max_budget_usd", cost=0.6, tokens=9)],
            "max_budget",
        ),
        (SCHEMA, [*ANSWERED[:1], ended("success", cost=0.1, tokens=9, structured={})], "ok"),
    ],
)
def test_only_a_call_with_a_schema_and_out_of_turns_gets_answer_turns(
    schema: Any,
    lookups: list[Message],
    outcome: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake = FakeSdk(lookups, ANSWERED)
    result = run_faked(monkeypatch, tmp_path, fake, request(schema=schema))
    assert result.outcome == outcome
    assert len(fake.calls) == 1


def test_no_answer_turns_when_the_lookups_spent_the_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spent = [*OUT_OF_TURNS[:2], ended("error_max_turns", cost=0.5, tokens=300)]
    fake = FakeSdk(spent, ANSWERED)
    result = run_faked(monkeypatch, tmp_path, fake, request(schema=SCHEMA))
    assert (result.outcome, result.structured) == ("max_budget", None)
    assert result.detail == "error_max_turns, and nothing left of the budget for the answer"
    assert result.usage.cost_usd == 0.5
    assert len(fake.calls) == 1


def test_an_answer_that_fails_says_it_was_asked_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    again = [*ANSWERED[:1], ended("error_max_structured_output_retries", cost=0.3, tokens=500)]
    fake = FakeSdk(OUT_OF_TURNS, again)
    result = run_faked(monkeypatch, tmp_path, fake, request(schema=SCHEMA))
    assert (result.outcome, result.structured) == ("invalid_output", None)
    assert result.detail == (
        "error_max_structured_output_retries, when asked for the answer after error_max_turns"
    )
    assert result.usage.cost_usd == 0.3


def test_the_time_limit_covers_the_answer_turns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeSdk(OUT_OF_TURNS, None)  # the answer never comes
    call = replace(request(schema=SCHEMA), budget=Budget(max_budget_usd=0.5, timeout_s=0.5))
    result = run_faked(monkeypatch, tmp_path, fake, call)
    assert (result.outcome, result.detail) == ("timeout", "took over 0.5 s")
    assert len(fake.calls) == 2
    # What the lookups cost is still counted: the ledger settles with it.
    assert (result.usage.input_tokens, result.usage.cost_usd) == (300, 0.2)
    assert result.turns == 2
