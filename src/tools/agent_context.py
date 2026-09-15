"""Which agent is making the current tool call (B80/B81 prerequisite).

The tool middleware sees a call but not its caller. The design knowledge base
has to record who did the work, and the duplicate-work guard counts refusals per
agent, so the identity must be available at call time.

A ``ContextVar`` rather than a global: networked peers run concurrently in
threads, and smolagents runs parallel tool calls in a ThreadPoolExecutor with
``copy_context()`` (smolagents/agents.py), so a context variable set around an
agent's ``run()`` follows every tool call that run makes -- and no other.
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentIdentity:
    name: str
    role: str = ""


_current: ContextVar[AgentIdentity | None] = ContextVar("avion_current_agent", default=None)


def current_agent() -> AgentIdentity | None:
    return _current.get()


def current_agent_name(default: str = "unknown") -> str:
    ident = _current.get()
    return ident.name if ident else default


@contextlib.contextmanager
def agent_scope(name: str, role: str = ""):
    """Mark every tool call made inside the block as made by this agent."""
    token = _current.set(AgentIdentity(name=str(name), role=str(role or "")))
    try:
        yield
    finally:
        _current.reset(token)
