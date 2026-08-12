"""Shared history and message data structures for multi-agent coordination."""

from dataclasses import dataclass, field


@dataclass
class ToolCallRecord:
    """Record of a single tool invocation within an agent turn."""

    tool_name: str
    inputs: dict
    output: str
    duration_seconds: float
    error: str | None = None


@dataclass
class AgentMessage:
    """A single agent turn in the coordination history."""

    agent_name: str
    content: str
    turn_number: int
    timestamp: float
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    duration_seconds: float = 0.0
    token_count: int | None = None
    is_retry: bool = False
    retry_of_turn: int | None = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)


class SharedHistory:
    """Append-only history of AgentMessage entries shared across coordination strategies."""

    def __init__(self):
        self._messages: list[AgentMessage] = []

    def append(self, message: AgentMessage) -> None:
        """Add a message to the history."""
        self._messages.append(message)

    def get_recent(self, n: int) -> list[AgentMessage]:
        """Get the last N messages."""
        return self._messages[-n:]

    def get_by_agent(self, name: str) -> list[AgentMessage]:
        """Get all messages from a specific agent."""
        return [m for m in self._messages if m.agent_name == name]

    def get_all(self) -> list[AgentMessage]:
        """Return full history as a new list."""
        return list(self._messages)

    def to_context_string(self, max_tokens: int = 3000) -> str:
        """Format history as a string for injection into agent prompts.

        Truncates from oldest if the output exceeds max_tokens (estimated
        as ~4 chars per token). Always includes the most recent messages.
        """
        max_chars = max_tokens * 4
        lines = []
        for m in self._messages:
            line = f"[Turn {m.turn_number}] {m.agent_name}: {m.content}"
            lines.append(line)

        result = "\n".join(lines)
        if len(result) > max_chars:
            # Truncate from the beginning, keeping recent context
            result = result[-max_chars:]
            first_newline = result.find("\n")
            if first_newline != -1:
                result = "...\n" + result[first_newline + 1 :]
            else:
                result = "..." + result
        return result

    @property
    def turn_count(self) -> int:
        """Number of messages in history."""
        return len(self._messages)

    def __len__(self) -> int:
        return len(self._messages)


def estimate_token_count(content: str, agent: object | None = None) -> int | None:
    """Token count for a message, from the agent's own usage when available.

    ``token_count`` was set on only 3 of the 11 AgentMessage construction sites,
    so `org_theory_metrics` treated it as unavailable and hardcoded three
    metrics to null with the warnings

        "orchestrator_token_growth: token_count is always null in current messages"
        "information_ratio: requires token_count on messages, always null in current runs"
        "per_stage_tokens (staged_pipeline): token_count always null"

    Task/prompt messages carry information too -- an orchestrator's instruction
    to a worker is exactly what "information asymmetry" is about -- so every
    message gets a count, not just agent replies.

    Prefers smolagents' real usage (``step.token_usage``), falling back to a
    length estimate. The estimate is marked by returning it only when there is
    content, so a genuinely empty message stays None rather than reading as
    zero-information.
    """
    if agent is not None:
        for attr in ("token_count", "total_tokens"):
            val = getattr(agent, attr, None)
            if isinstance(val, int) and val > 0:
                return val
        mem = getattr(agent, "memory", None)
        steps = getattr(mem, "steps", None) if mem else None
        if steps:
            total = 0
            for step in steps:
                usage = getattr(step, "token_usage", None)
                if isinstance(usage, dict):
                    total += usage.get("total_tokens", 0) or (
                        usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                    )
                elif isinstance(usage, int):
                    total += usage
            if total:
                return total
    if content:
        return max(1, len(content) // 4)
    return None
