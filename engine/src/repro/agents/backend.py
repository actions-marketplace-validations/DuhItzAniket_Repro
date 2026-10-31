"""The interface every LLM call goes through (G01; ADR-0007, ARCHITECTURE §10.5).

An agent call is a request (a role, a system prompt, the user's text, the read-only tools it
may call, an optional output schema, a model, the prompt's version and a budget) and a result
(the structured output or text, how the run ended, its usage). The default implementation is
the Claude Agent SDK (``repro.agents.sdk``); cassettes (``repro.agents.cassette``) and tests
implement the same protocol, so the stages that use agents never depend on how the model is
reached.

``on_progress`` reports what the agent is doing while it runs (thinking, a tool call): the
``agent.state`` events the cockpit's dispatch and workspace panels draw (``repro.agents.events``).
"""

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol

Role = Literal["read", "build", "review", "gap", "probe", "qa"]
#: How a run ended: ``ok`` with its output; or why there is none.
Outcome = Literal["ok", "max_turns", "max_budget", "timeout", "invalid_output", "error"]


@dataclass(frozen=True, slots=True)
class Budget:
    """What one agent call may spend; every call has one (ADR-0007).

    ``max_turns`` bounds the model's responses. A call with an output schema always gets to
    answer: the OpenAI-compatible backend keeps the last of them for its answer, and the Agent
    SDK backend, where any turn can answer, follows lookups that used them all with up to two
    turns for the answer alone (``repro.agents.sdk.ANSWER_TURNS``). The dollar budget and the
    time limit hold for the whole call."""

    max_turns: int = 8
    max_budget_usd: float = 1.0
    timeout_s: float = 600.0

    def __post_init__(self) -> None:
        if self.max_turns < 1 or self.max_budget_usd <= 0 or self.timeout_s <= 0:
            raise ValueError("a budget's limits must be positive")


#: A tool's name: words of lowercase letters and digits joined by single underscores.
TOOL_NAME: Final = re.compile(r"(?=.{1,48}$)[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
#: A prompt's version: its file's name and version number, as ``reviewer/3`` (G02).
PROMPT_VERSION: Final = re.compile(r"[a-z][a-z0-9_-]{0,47}/[1-9][0-9]{0,5}")
#: A tool handler: its validated input, then the text it returns to the model.
ToolHandler = Callable[[Mapping[str, Any]], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class ReproTool:
    """A read-only, in-process tool the agent may call (``mcp__repro__<name>``)."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    handler: ToolHandler

    def __post_init__(self) -> None:
        if not TOOL_NAME.fullmatch(self.name):
            raise ValueError(f"tool names are lower_snake_case, up to 48 characters: {self.name!r}")


@dataclass(frozen=True, slots=True)
class AgentRequest:
    role: Role
    system_prompt: str
    prompt: str
    model: str
    #: The version of the prompt the call was built from (``repro.agents.prompts``).
    prompt_version: str
    budget: Budget = field(default_factory=Budget)
    tools: Sequence[ReproTool] = ()
    #: JSON Schema (draft-07, ``$ref``-free) the final answer must satisfy, or None for text.
    output_schema: Mapping[str, Any] | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None

    def __post_init__(self) -> None:
        if not PROMPT_VERSION.fullmatch(self.prompt_version):
            raise ValueError(f"a prompt version is name/number: {self.prompt_version!r}")


@dataclass(frozen=True, slots=True)
class Usage:
    """Client-side estimates, never billing data (research/04 §1.8)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    #: Whether ``cost_usd`` rests on a known price; False: the tokens are counted, the cost is
    #: unknown (0 here, and no ``cost.estimate``; G10, ADR-0016).
    priced: bool = True


@dataclass(frozen=True, slots=True)
class AgentResult:
    outcome: Outcome
    #: The schema-valid answer (with an ``output_schema``), else None.
    structured: Any = None
    text: str = ""
    turns: int = 0
    usage: Usage = field(default_factory=Usage)
    #: Why there is no output, in a line (empty when ``ok``).
    detail: str = ""
    #: Tool calls the model made, in order: (tool name, whether it was allowed to run).
    tool_calls: tuple[tuple[str, bool], ...] = ()
    #: ``usage`` per model the call used (subagents included, research/04 §1.8).
    by_model: Mapping[str, Usage] = field(default_factory=dict)
    #: Served from a cassette: ``usage`` is what the call cost when it was recorded.
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class Progress:
    """What an agent is doing now (``agent.state``'s ``thinking`` and ``tool_call``)."""

    state: Literal["thinking", "tool_call"]
    #: The model turn it happened in, from 1 (a turn is one model response).
    turn: int
    tool: str | None = None
    detail: str | None = None


OnProgress = Callable[[Progress], None]


class LLMBackend(Protocol):
    """Runs one agent call to its end; never raises for what the model does (the result says
    so), only for a backend that cannot run at all."""

    async def run(
        self, request: AgentRequest, on_progress: OnProgress | None = None
    ) -> AgentResult: ...
