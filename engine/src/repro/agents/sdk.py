"""The default ``LLMBackend``: the Claude Agent SDK, locked down (G01; ADR-0007).

Each call runs in a workspace of its own under ``scratch``: an empty working directory, a
Claude configuration directory and the system prompt file, removed when the call ends. Repro's
tools run in-process as one SDK MCP server (``mcp__repro__*``); everything else is closed by
``repro.agents.lockdown``. The SDK starts one Claude Code process per call (research/04 §1.10:
about 1 GiB of memory each), which ``repro.agents.dispatch`` bounds.

**A call with an output schema is always asked for its answer.** Its lookups may use every one
of its ``max_turns``, and in any of them the model may answer through ``StructuredOutput``. A
model still looking things up when they run out (``error_max_turns``) is then asked for its
answer, as on the OpenAI-compatible backend, which keeps its last turns for it: the session is
resumed for ``ANSWER_TURNS`` more turns (the answer, and the CLI's retry of one off the schema),
told ``ANSWER_NOW`` and offered no tool but ``StructuredOutput``, under the same lockdown, within
what is left of the call's dollar budget and time. Seen on real pull requests: Haiku reviewers
that spent all four of their turns on lookups ended with no verdict, and their claims were not
judged. The step is shown as a ``thinking`` progress that says so, its turns are counted in the
call's, and its cost in the call's usage. Its answer is checked like any other: against the
schema by the CLI, and for its citations by the caller (``repro.agents.validate``).

A call's ``turns`` are the model's responses, which ``max_turns`` limits. The CLI's own
``num_turns`` counts differently (observed with CLI 2.1.300: 8 for three responses that made
seven tool calls).

What the model does never raises: a run that hits its turn or dollar budget, its time limit, or
returns no valid structured output is a result with that outcome. Only a missing CLI raises.
After an error result, the CLI exits non-zero and the SDK raises ``ResultError``; the result it
already yielded says what happened, so that is what the call reports.
"""

import asyncio
import shutil
import tempfile
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import aclosing
from dataclasses import replace
from pathlib import Path
from typing import Any, Final, cast

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    CLINotFoundError,
    Message,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolAnnotations,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)
from claude_agent_sdk.types import McpSdkServerConfig

from repro import __version__
from repro.agents.backend import (
    AgentRequest,
    AgentResult,
    OnProgress,
    Outcome,
    Progress,
    ReproTool,
    Usage,
)
from repro.agents.lockdown import SERVER, STRUCTURED_OUTPUT, lockdown_options, permitted_tools

#: How each ``ResultMessage.subtype`` that is not a success ends a run.
SUBTYPES: Final[dict[str, Outcome]] = {
    "error_max_turns": "max_turns",
    "error_max_budget_usd": "max_budget",
    "error_max_structured_output_retries": "invalid_output",
}
#: Characters of text or tool input shown as an ``agent.state`` detail.
DETAIL: Final = 200
#: The turns a call with an output schema is given after its lookups used ``max_turns``
#: without an answer: the answer's, and the CLI's retry of an answer off the schema.
ANSWER_TURNS: Final = 2
#: What the model is told in the first of them.
ANSWER_NOW: Final = (
    "Your turns for looking things up are spent, and no tool will run now but "
    f"{STRUCTURED_OUTPUT}. Give your final answer through it now, from what you have been shown."
)


def _sdk_tool(spec: ReproTool) -> Any:
    async def handle(arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            text = await spec.handler(arguments)
        except Exception as exc:  # noqa: BLE001 - the model is told, the run goes on
            return {"content": [{"type": "text", "text": f"error: {exc}"}], "is_error": True}
        return {"content": [{"type": "text", "text": text}]}

    return tool(
        spec.name,
        spec.description,
        dict(spec.input_schema),
        annotations=ToolAnnotations(readOnlyHint=True),
    )(handle)


def _server(tools: tuple[ReproTool, ...]) -> McpSdkServerConfig | None:
    if not tools:
        return None
    return create_sdk_mcp_server(SERVER, version=__version__, tools=[_sdk_tool(t) for t in tools])


def _count(values: Mapping[str, Any], name: str) -> int:
    return int(values.get(name) or 0)


def _usage(result: ResultMessage) -> tuple[Usage, dict[str, Usage]]:
    """The call's usage, in all and per model. ``model_usage`` counts subagents too, so it is
    the one to account with (research/04 §1.8); ``usage`` stands in when it is absent."""
    by_model = {
        str(model): Usage(
            input_tokens=_count(values, "inputTokens"),
            output_tokens=_count(values, "outputTokens"),
            cache_read_tokens=_count(values, "cacheReadInputTokens"),
            cost_usd=float(values.get("costUSD") or 0.0),
        )
        for model, values in (result.model_usage or {}).items()
    }
    if by_model:
        total = Usage(
            input_tokens=sum(u.input_tokens for u in by_model.values()),
            output_tokens=sum(u.output_tokens for u in by_model.values()),
            cache_read_tokens=sum(u.cache_read_tokens for u in by_model.values()),
            cost_usd=float(result.total_cost_usd or sum(u.cost_usd for u in by_model.values())),
        )
        return total, by_model
    usage: Mapping[str, Any] = result.usage or {}
    total = Usage(
        input_tokens=_count(usage, "input_tokens"),
        output_tokens=_count(usage, "output_tokens"),
        cache_read_tokens=_count(usage, "cache_read_input_tokens"),
        cost_usd=float(result.total_cost_usd or 0.0),
    )
    return total, {}


def _outcome(result: ResultMessage, expects_structure: bool) -> tuple[Outcome, str]:
    if result.subtype in SUBTYPES:
        return SUBTYPES[result.subtype], result.subtype
    if result.is_error:
        status = f" (HTTP {result.api_error_status})" if result.api_error_status else ""
        return "error", f"{result.subtype}{status}: {'; '.join(result.errors or [])}"[:500]
    if expects_structure and result.structured_output is None:
        return "invalid_output", "no structured output"  # a success without it is a failure
    return "ok", ""


class _Transcript:
    """What a call's sessions have said so far: turns, tool calls, text and results."""

    def __init__(self, permitted: frozenset[str], on_progress: OnProgress | None) -> None:
        self.permitted = permitted
        self.on_progress = on_progress
        self.turn = 0
        self.calls: list[tuple[str, bool]] = []
        self.texts: list[str] = []
        self.result: ResultMessage | None = None
        #: How the lookups ended, once the call was asked for its answer.
        self.lookups: ResultMessage | None = None
        self._last_id: str | None = None

    def _progress(self, update: Progress) -> None:
        if self.on_progress is not None:
            self.on_progress(update)

    def take(self, message: Message) -> None:
        if isinstance(message, ResultMessage):
            self.result = message
            return
        if not isinstance(message, AssistantMessage):
            return
        # One response can arrive as several messages with one id.
        if message.message_id is None or message.message_id != self._last_id:
            self.turn += 1
        self._last_id = message.message_id
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                self.calls.append((block.name, block.name in self.permitted))
                detail = f"{block.name}({block.input})"[:DETAIL]
                self._progress(Progress("tool_call", self.turn, block.name, detail))
            elif isinstance(block, TextBlock):
                self.texts.append(block.text)
                said = block.text[:DETAIL] or None
                self._progress(Progress("thinking", self.turn, detail=said))
            elif isinstance(block, ThinkingBlock):
                self._progress(Progress("thinking", self.turn))

    def answering(self, permitted: frozenset[str]) -> None:
        """The lookups are over and the answer's turns begin: only ``permitted`` may run now,
        and the step is shown as the next turn's ``thinking``."""
        self.lookups, self.result = self.result, None
        self.permitted = permitted
        said = f"no answer after {self.turn} turns of lookups: asked for it now"
        self._progress(Progress("thinking", self.turn + 1, detail=said))

    def failed(self, outcome: Outcome, detail: str) -> AgentResult:
        """A call that ended without a result of its own. If its lookups ended, their usage is
        what it is known to have cost."""
        usage, by_model = _usage(self.lookups) if self.lookups is not None else (Usage(), {})
        return AgentResult(
            outcome,
            turns=self.turn,
            usage=usage,
            detail=detail,
            tool_calls=tuple(self.calls),
            by_model=by_model,
        )

    def ended(self, expects_structure: bool) -> AgentResult:
        result = self.result
        if result is None:
            return self.failed("error", "the session ended without a result")
        outcome, detail = _outcome(result, expects_structure)
        if self.lookups is not None and outcome != "ok":
            detail = f"{detail}, when asked for the answer after error_max_turns"[:500]
        # A resumed session's result counts the usage of the session it resumed too (observed
        # with CLI 2.1.300), so the last result's usage is the whole call's.
        usage, by_model = _usage(result)
        return AgentResult(
            outcome=outcome,
            structured=result.structured_output if outcome == "ok" else None,
            text=result.result or "\n".join(self.texts),
            turns=self.turn,
            usage=usage,
            detail=detail,
            tool_calls=tuple(self.calls),
            by_model=by_model,
        )


async def _session(prompt: str, options: ClaudeAgentOptions, transcript: _Transcript) -> str | None:
    """Runs one CLI session into ``transcript``; why it ended without a result, if it did."""
    try:
        stream = query(prompt=prompt, options=options)
        async with aclosing(cast(AsyncGenerator[Message], stream)) as messages:
            async for message in messages:
                transcript.take(message)
    except CLINotFoundError:
        raise
    except ClaudeSDKError as exc:
        result = transcript.result
        if result is None or not result.is_error:  # not the exit after an error result
            return f"{type(exc).__name__}: {exc}"[:500]
    return None


def _answer_request(request: AgentRequest, lookups: ResultMessage) -> AgentRequest | None:
    """The request of a call's answer turns, after lookups that ran out of turns: no Repro
    tool, ``ANSWER_TURNS`` turns, and what is left of the call's dollar budget (a resumed
    session counts its spending against its limit from zero, observed with CLI 2.1.300).
    None when nothing is left."""
    left = request.budget.max_budget_usd - float(lookups.total_cost_usd or 0.0)
    if left <= 0:
        return None
    budget = replace(request.budget, max_turns=ANSWER_TURNS, max_budget_usd=left)
    return replace(request, tools=(), budget=budget)


class AgentSdkBackend:
    """Runs agent calls through the Claude Agent SDK, each in a workspace under ``scratch``."""

    def __init__(self, scratch: Path, *, cli_path: Path | None = None) -> None:
        self.scratch = scratch
        self.cli_path = cli_path

    async def run(
        self, request: AgentRequest, on_progress: OnProgress | None = None
    ) -> AgentResult:
        self.scratch.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix="agent-", dir=self.scratch))
        try:
            return await self._run(request, workspace, on_progress)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def _options(self, workspace: Path) -> Callable[[AgentRequest], ClaudeAgentOptions]:
        """The locked-down options of a session of a call in ``workspace``: the same working
        and configuration directories for every session of the call, so one can resume
        another."""
        cwd, config = workspace / "cwd", workspace / "config"
        cwd.mkdir()
        config.mkdir()
        prompt_file = workspace / "system.md"

        def options(request: AgentRequest) -> ClaudeAgentOptions:
            prompt_file.write_text(request.system_prompt, encoding="utf-8")
            built = lockdown_options(
                request,
                cwd=cwd,
                config_dir=config,
                system_prompt_file=prompt_file,
                server=_server(tuple(request.tools)),
            )
            if self.cli_path is not None:
                built.cli_path = str(self.cli_path)
            return built

        return options

    async def _run(
        self, request: AgentRequest, workspace: Path, on_progress: OnProgress | None
    ) -> AgentResult:
        options = self._options(workspace)
        transcript = _Transcript(permitted_tools(request), on_progress)
        expects_structure = request.output_schema is not None
        try:
            async with asyncio.timeout(request.budget.timeout_s):
                error = await _session(request.prompt, options(request), transcript)
                lookups = transcript.result
                if (
                    error is None
                    and expects_structure
                    and lookups is not None
                    and lookups.subtype == "error_max_turns"
                ):
                    answer = _answer_request(request, lookups)
                    if answer is None:
                        spent = transcript.ended(expects_structure)
                        why = "error_max_turns, and nothing left of the budget for the answer"
                        return replace(spent, outcome="max_budget", detail=why)
                    transcript.answering(permitted_tools(answer))
                    resumed = options(answer)
                    resumed.resume = lookups.session_id
                    error = await _session(ANSWER_NOW, resumed, transcript)
        except TimeoutError:
            return transcript.failed("timeout", f"took over {request.budget.timeout_s:g} s")
        if error is not None:
            return transcript.failed("error", error)
        return transcript.ended(expects_structure)
