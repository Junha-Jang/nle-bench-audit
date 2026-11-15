"""Protocol definitions for NLE-Bench agent abstraction.

Defines protocols for two benchmark tracks:
- Canonical Track: Agent makes tool calls using 25 canonical tools
- Open Track: Agent directly produces final EditProject state
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from nlebench.models import EditProject


# ============================================================
# Tool Schema and Tool Call Types
# ============================================================

@dataclass
class ToolSchema:
    """Schema for a canonical tool."""
    name: str
    description: str
    parameters: dict  # JSON Schema for parameters

    def to_openai_format(self) -> dict:
        """Convert to OpenAI function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }


@dataclass
class ToolCall:
    """A single tool call made by an agent."""
    name: str
    arguments: dict
    id: str = ""  # Optional call ID for tracking


@dataclass
class AgentResponse:
    """Response from an agent execution."""
    message: str = ""
    tool_calls: list[ToolCall] | None = None
    final_state: EditProject | None = None  # For open track
    token_usage: Any = None  # Provider-specific token info


# ============================================================
# Canonical Track Protocol
# ============================================================

@runtime_checkable
class CanonicalAgent(Protocol):
    """Protocol for Canonical Track agents.

    Agents on the Canonical Track receive:
    - Initial EditProject state
    - List of 25 canonical tool schemas
    - User messages

    They produce:
    - Tool calls that NLE-Bench executes against the state
    """

    async def generate_response(
        self,
        state: EditProject,
        tools: list[ToolSchema],
        messages: list[dict],  # [{"role": "user", "content": "..."}]
    ) -> AgentResponse:
        """Generate tool calls based on the current state and messages.

        Args:
            state: Current EditProject state (read-only reference)
            tools: Available canonical tool schemas
            messages: Conversation history

        Returns:
            AgentResponse with tool_calls to execute
        """
        ...


# ============================================================
# Multi-turn Canonical Track Protocol (next-generation harness)
# ============================================================

@runtime_checkable
class MultiTurnCanonicalAgent(Protocol):
    """Protocol for the *closed-loop* Canonical Track.

    Motivation
    ----------
    The frozen (published) Canonical Track is **open-loop**: ``TrackRunner.
    _run_canonical`` calls :meth:`CanonicalAgent.generate_response` exactly once
    and never feeds tool return values back to the model. A model therefore
    cannot learn the identifier of an entity it just created, so any task of the
    form "create X, then modify X" is unsolvable except by guessing an id.

    This protocol describes the fixed harness: the runner executes each tool
    call and appends its **return value** to the conversation as a
    ``{"role": "tool", ...}`` message before asking the agent again.

    Contract differences vs. :class:`CanonicalAgent`
    ------------------------------------------------
    * ``messages`` is no longer user-only. It is the full running conversation
      and may contain ``assistant`` messages (with a ``tool_calls`` list in
      OpenAI function-calling shape) and ``tool`` messages whose ``content`` is
      the JSON-encoded handler result, e.g.
      ``{"success": true, "clip_id": "clip_3"}``.
    * ``state`` is the *current* (already mutated) project, not the initial one.
    * The agent is called repeatedly until it emits no tool calls or the turn /
      token budget is exhausted.

    The method name is deliberately identical to :class:`CanonicalAgent` so that
    existing providers can be dropped into the multi-turn loop unchanged; only
    the contract on ``messages`` widens. Because both protocols are structurally
    identical, ``isinstance`` cannot distinguish them — the *track* argument
    selects the loop, not the type.
    """

    async def generate_response(
        self,
        state: EditProject,
        tools: list[ToolSchema],
        messages: list[dict],
    ) -> AgentResponse:
        """Generate the next batch of tool calls given the running conversation.

        Args:
            state: Current EditProject state, reflecting all tool calls executed
                so far in this episode.
            tools: Available canonical tool schemas.
            messages: Full conversation so far — user, assistant (with
                ``tool_calls``) and ``tool`` (result) messages.

        Returns:
            AgentResponse. Returning no ``tool_calls`` terminates the episode.
        """
        ...


@dataclass
class MultiTurnBudget:
    """Turn and token budget for one multi-turn episode.

    ``max_turns`` default rationale (measured, not guessed): in the frozen
    single-shot Canonical logs (119,200 rows,
    ``per_scenario_results_redacted.csv``) the per-episode ``tool_call_count``
    has p50=1, p75=2, p90=3, p95=3, p99=5, max=55. If a closed-loop model emits
    roughly one tool call per turn, covering the 99th percentile needs 5 tool
    turns plus one final no-tool turn = 6. We add two turns of headroom for a
    ``query_state`` probe and one retry after a failed call, giving **8**. This
    bounds the worst-case per-episode cost at 8 model calls while leaving <1% of
    episodes budget-limited.
    """

    max_turns: int = 8
    max_total_tokens: int | None = None  # None = no token cap


@dataclass
class TurnRecord:
    """One model call inside a multi-turn episode."""

    turn_index: int
    message: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "turn_index": self.turn_index,
            "message": self.message,
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass
class MultiTurnTrace:
    """Turn/token accounting for one multi-turn episode.

    Recorded on the ExecutionResult so re-run cost can be measured directly
    rather than modelled.
    """

    turns: list[TurnRecord] = field(default_factory=list)
    max_turns: int = 0
    stop_reason: str = ""  # no_tool_calls | max_turns | token_budget
    token_budget: int | None = None

    @property
    def turns_used(self) -> int:
        return len(self.turns)

    @property
    def input_tokens(self) -> int:
        return sum(t.input_tokens for t in self.turns)

    @property
    def output_tokens(self) -> int:
        return sum(t.output_tokens for t in self.turns)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def tool_call_count(self) -> int:
        return sum(len(t.tool_calls) for t in self.turns)

    def to_dict(self) -> dict:
        return {
            "turns_used": self.turns_used,
            "max_turns": self.max_turns,
            "stop_reason": self.stop_reason,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "token_budget": self.token_budget,
            "token_budget_exhausted": self.stop_reason == "token_budget",
            "tool_call_count": self.tool_call_count,
            "turns": [t.to_dict() for t in self.turns],
        }


# ============================================================
# Open Track Protocol
# ============================================================

@runtime_checkable
class OpenAgent(Protocol):
    """Protocol for Open Track agents.

    Agents on the Open Track receive:
    - Initial EditProject state
    - User messages

    They produce:
    - Final EditProject state directly (any method allowed)
    """

    async def generate_state(
        self,
        initial_state: EditProject,
        messages: list[dict],  # [{"role": "user", "content": "..."}]
    ) -> AgentResponse:
        """Generate final state based on initial state and messages.

        Args:
            initial_state: Starting EditProject state
            messages: Conversation history

        Returns:
            AgentResponse with final_state
        """
        ...


# ============================================================
# Legacy Protocol (backwards compatibility)
# ============================================================

@runtime_checkable
class EditAgent(Protocol):
    """Protocol for editing agents that can be benchmarked.

    DEPRECATED: Use CanonicalAgent or OpenAgent instead.

    Any agent that implements this interface can be used with NLE-Bench.
    This is the legacy interface where agents maintain internal state.
    """

    @property
    def state(self) -> EditProject:
        """Current EditProject state of the agent."""
        ...

    async def chat(self, message: str) -> Any:
        """Process a user message and modify the EditProject.

        Args:
            message: User instruction for editing

        Returns:
            Agent response (implementation-specific)
        """
        ...


@runtime_checkable
class ToolEnabledAgent(EditAgent, Protocol):
    """EditAgent that exposes its tool schemas.

    Agents implementing this protocol declare the tools they support,
    enabling NLE-Bench to verify tool usage patterns.
    """

    @property
    def tools(self) -> list[dict]:
        """List of tool schemas in OpenAI function-calling format."""
        ...


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM providers."""

    @property
    def model(self) -> str:
        """Model identifier."""
        ...
