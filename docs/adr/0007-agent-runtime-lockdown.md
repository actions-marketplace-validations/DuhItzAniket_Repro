# ADR-0007: Locked-down agent runtime behind an `LLMBackend` interface

- **Status:** accepted (approved by the project owner on 2026-10-13, including the recommendation in "Open question": ship the Agent SDK backend first and decide on a direct-API backend from G09 eval data)
- **Date:** 2026-10-13
- **Phase:** A01 (implemented in G01–G07)

## Context
The decided stack names the Claude Agent SDK (Python). Facts verified on 2026-10-13
(research/04 §1):
- `claude-agent-sdk` 0.2.165 bundles the Claude Code CLI and runs one subprocess per session,
  with about 1 GiB of RAM per agent recommended.
- `allowed_tools` only pre-approves tools. Restriction needs `tools=[]`, `permission_mode="dontAsk"`
  and a `PreToolUse` deny hook.
- By default the SDK loads `CLAUDE.md`, hooks and skills from `cwd`. A PR worktree as `cwd` is
  therefore an injection vector.
- Structured output is validated against JSON Schema **draft-07** with re-prompting. It is not
  constrained decoding.
- The Bash sandbox does not run on native Windows. That doesn't matter if agents have no Bash.

Reviewer agents do not need Claude Code's built-in tools at all. They need a handful of
read-only evidence tools.

## Decision
1. All LLM calls go through an `LLMBackend` protocol. The **default implementation is the
   Claude Agent SDK**, as decided in the stack.
2. Lockdown, enforced and tested:
   - `tools=[]`, with the only tools being in-process `mcp__repro__*` read-only tools;
   - `permission_mode="dontAsk"` and a `PreToolUse` deny hook for anything else;
   - `setting_sources=[]`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, a per-run `CLAUDE_CONFIG_DIR`
     and `verbatim_prompts=True`;
   - `cwd` is an empty scratch directory;
   - budget limits on every call.
3. Verdict output schemas are generated per claim. Cited evidence is an **enum of valid IDs**
   with `minItems: 1`. Quotes are checked as exact substrings. When validation fails, the engine
   emits `verdict.rejected` and a missing item. It never accepts an uncited verdict.
4. CI uses recorded cassettes, and a missing cassette is a test failure.

## Open question for the project owner
Should reviewer agents run on a **direct Anthropic Messages API backend** instead of the Agent
SDK? Benefits: constrained-decoding structured output, no ~1 GiB subprocess per agent, no
bundled CLI binary. Cost: a second integration path and loss of SDK conveniences such as hooks
and session management. Recommendation: build the protocol now, ship the Agent SDK backend
first (G01), and benchmark both on the agent eval set in G08 before deciding. Choosing the direct
API would be a stack change and needs approval.

## Consequences
Agent concurrency defaults to `min(12, free RAM in GiB)` on the SDK backend. The dispatch panel
still shows 12 slots and marks queued agents as `waiting`.

## Implementation notes (G01, 2026-10-15)
- Built on `claude-agent-sdk` 0.2.167 (CLI 2.1.300) in `repro.agents.lockdown` and
  `repro.agents.sdk`, with every point of decision 2 in place, plus `strict_mcp_config`, no
  plugins or skills, `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and a wall-clock timeout per call.
- The deny hook permits exactly a call's own tools, not any `mcp__repro__*` name, and
  `StructuredOutput` when the call has an output schema: the CLI delivers `output_format`
  through that tool, and denying it left the run looping to its turn limit.
- Tested against the bundled CLI with a local stand-in for the Messages API. Each planted
  `CLAUDE.md`, hook, skill, subagent, command and `.mcp.json` is first shown to load on an
  unlocked CLI, then not to load under the lockdown (`engine/tests/test_agents_cli.py`; the
  phase record `docs/phases/G01-llm-backend.md` has the evidence).
- Decision 4 (G02, `repro.agents.cassette`): cassettes are keyed by prompt version, model and an
  input digest taken with run-specific IDs normalised, and a replay maps the recording's IDs to
  the run's; a missing cassette raises, and replay has no backend through which to call.
- The answer's turns (fix after 1.5.0, 2026-10-31, `repro.agents.sdk`): a call with an output
  schema whose lookups end `error_max_turns` is resumed (`resume`, the call's own session, in
  its own configuration directory) for up to two turns. It is told to answer now, and
  `StructuredOutput` is the only tool in its context and the only one the gate permits. Every
  other point of decision 2 holds for that session as for the first. Its dollar budget is what
  the first session left, and the call's time limit covers both.
