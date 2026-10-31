"""The agent runtime's lockdown (G01; ADR-0007, ARCHITECTURE §10.2, research/04 §1.3).

Reviewer agents read evidence; they never need Claude Code's own tools, settings or memory, and
the target repository is untrusted. Every option and variable below closes one way in:

- ``tools=[]``: no built-in tool (Bash, Read, Edit, Agent, ...) is in the model's context; the
  only tools are the call's own from Repro's in-process MCP server, ``mcp__repro__<name>``, each
  pre-approved by its full name (``allowed_tools``), and, with an output schema, the CLI's
  ``StructuredOutput``, through which the model gives its final answer (observed with CLI
  2.1.300: the tool is what ``output_format`` adds, and a run cannot finish without it);
- ``permission_mode="dontAsk"``: anything not pre-approved is denied, never asked about; and a
  ``PreToolUse`` hook denies every other tool, a Repro tool the call was not given included,
  even if one got into context (a hook's deny applies in every permission mode);
- ``strict_mcp_config=True``, no plugins, no skills: no MCP server, plugin or skill but Repro's;
- ``setting_sources=[]`` (an explicit empty list: left unset, the SDK loads user and project
  settings) and ``CLAUDE_CODE_DISABLE_CLAUDE_MDS=1``, ``CLAUDE_CODE_DISABLE_AUTO_MEMORY=1``, a
  per-run ``CLAUDE_CONFIG_DIR``: no settings, hooks, CLAUDE.md or memory from anywhere;
- ``cwd``: an empty per-run directory, never a snapshot or a worktree;
- ``verbatim_prompts=True``: the prompt is sent as it is (no ``@path`` reads, no slash commands);
- ``CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1``: no update checks, telemetry or error reports;
- no subagents: ``tools=[]`` leaves no Agent tool to start one, and as a backstop
  ``CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1`` (one layer, which cannot nest; the variable takes
  a positive number and ignores anything else, so 1 is its floor) with
  ``CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS=1``.

A call resumed for its answer (``repro.agents.sdk``) runs under these same options, built for a
request with no Repro tool: ``StructuredOutput`` is all its context offers and all the gate
lets through.

The environment variables are Claude Code's documented ones (code.claude.com/docs/en/env-vars,
read 2026-10-15).
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookCallback,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
)
from claude_agent_sdk.types import McpSdkServerConfig, SyncHookJSONOutput

from repro.agents.backend import AgentRequest

#: Repro's in-process MCP server: its tools are ``mcp__repro__<name>``.
SERVER: Final = "repro"
TOOL_PREFIX: Final = f"mcp__{SERVER}__"
#: The tool the CLI adds for ``output_format``: the model's answer, not a capability.
STRUCTURED_OUTPUT: Final = "StructuredOutput"


def lockdown_env(config_dir: Path) -> dict[str, str]:
    """The variables every agent subprocess runs with (on top of the inherited environment,
    which carries the API credentials: a per-run configuration directory holds no login)."""
    return {
        "CLAUDE_CONFIG_DIR": str(config_dir),
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1",
        "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "1",
    }


def permitted_tools(request: AgentRequest) -> frozenset[str]:
    """The full names of the tools one call may run: its own Repro tools, and
    ``StructuredOutput`` when it asks for structured output. Nothing else, ever."""
    names = {f"{TOOL_PREFIX}{tool.name}" for tool in request.tools}
    if request.output_schema is not None:
        names.add(STRUCTURED_OUTPUT)
    return frozenset(names)


def tool_gate(permitted: frozenset[str]) -> HookCallback:
    """A ``PreToolUse`` hook that denies every tool outside ``permitted``, and leaves the
    permitted ones to the allow rules (their full names are pre-approved)."""

    async def gate(
        hook_input: HookInput, _tool_use_id: str | None, _context: HookContext
    ) -> HookJSONOutput:
        name = str(hook_input.get("tool_name", ""))
        if name in permitted:
            return SyncHookJSONOutput()
        return SyncHookJSONOutput(
            hookSpecificOutput={
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"{name} is not one of this call's tools",
            }
        )

    return gate


def lockdown_options(
    request: AgentRequest,
    *,
    cwd: Path,
    config_dir: Path,
    system_prompt_file: Path,
    server: McpSdkServerConfig | None,
) -> ClaudeAgentOptions:
    """The options of one locked-down agent call. ``cwd`` must be an empty directory of the
    run's own; the system prompt travels as a file (Windows caps a command line near 32 KB)."""
    permitted = permitted_tools(request)
    output: Mapping[str, Any] | None = (
        {"type": "json_schema", "schema": dict(request.output_schema)}
        if request.output_schema is not None
        else None
    )
    return ClaudeAgentOptions(
        tools=[],
        allowed_tools=sorted(permitted),
        mcp_servers={SERVER: server} if server is not None else {},
        strict_mcp_config=True,
        plugins=[],
        skills=None,
        permission_mode="dontAsk",
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[tool_gate(permitted)])]},
        setting_sources=[],
        verbatim_prompts=True,
        cwd=str(cwd),
        env=lockdown_env(config_dir),
        system_prompt={"type": "file", "path": str(system_prompt_file)},
        model=request.model,
        effort=request.effort,
        max_turns=request.budget.max_turns,
        max_budget_usd=request.budget.max_budget_usd,
        output_format=dict(output) if output is not None else None,
    )
