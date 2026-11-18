"""The state of ONE tool call, from resolve_request to intercept_response (B361).

Why not a ``threading.local`` (what ``data_plane._call_ctx`` and ``coupling_contract._call_start`` were): every tool
call runs its whole middleware in a ``call_watchdog`` worker thread taken from a POOL (``bounded`` -> ``_outer_pool``).

* The threads are reused. A thread-local attribute one call set and did not clear (a call refused or raising between
  its pre-hook and its post-hook; an attribute set only for some tools and read for others) was still there when the
  next call -- possibly another agent's -- ran on that worker.
* A per-call note kept in the shared ``data_store`` (``_coupled_override_last``, ``_geometry_authority_last``,
  ``_su2_kept_back_last``, ``_target_cl_asked``, ``_pinned_overrides``) was popped by whichever call's post-hook ran
  next: the framework's own set_aircraft_parameters inside a set_inputs took the agent's Fn_DES / fixed-input notes,
  and two networked peers calling the same tool at once read each other's.

A scope is opened by the middleware at the start of every call (``open_call_scope``) in that call's own context
(``call_watchdog`` runs each call in a ``contextvars.copy_context()``), so the pre-hook and the post-hook of one call
share it, a nested framework call (its own watchdog thread, its own scope) cannot see or clobber it, parallel peers
never share one, and nothing outlives the call. Values that must flow INTO a nested call (the cruise-point design
basis, B360) are not kept here; they are ContextVars of their own, which the nested call's copied context inherits.

Outside the middleware (tests, the runner calling data-plane functions directly) a scope is created on first use in
the current context and lives as long as that context, which is what the thread-local gave those callers.
"""
from __future__ import annotations

import contextvars


class CallScope:
    """Attributes of one tool call (free-form, as the thread-local was) plus ``notes``: what the call's reply says."""

    def __init__(self):
        self.notes: dict = {}


_scope: contextvars.ContextVar[CallScope | None] = contextvars.ContextVar("avion_call_scope", default=None)


def current_scope() -> CallScope:
    scope = _scope.get()
    if scope is None:
        scope = CallScope()
        _scope.set(scope)
    return scope


# B362: True inside a tool call's middleware. A call that STARTS while it is True was issued by the framework from
# inside another call (the cruise-point set + ask in set_inputs / run_cycle, B187 / B272 re-applies, the engine deck,
# auto-export, auto-measure); every call runs in a copy of its caller's context (call_watchdog), so the flag reaches it.
_inside_call: contextvars.ContextVar[bool] = contextvars.ContextVar("avion_inside_tool_call", default=False)


def open_call_scope() -> tuple:
    """A fresh scope for the tool call starting now (middleware), in the current context; records whether the
    framework issued it (``scope.framework_initiated``) and marks the context as inside a call for nested calls.
    Pass the result to close_call_scope."""
    scope = CallScope()
    scope.framework_initiated = bool(_inside_call.get())
    return _scope.set(scope), _inside_call.set(True)


def close_call_scope(tokens) -> None:
    scope_token, inside_token = tokens
    _inside_call.reset(inside_token)
    _scope.reset(scope_token)


def open_plain_scope() -> contextvars.Token:
    """A fresh scope without marking the context as inside a call (tests calling the data plane directly)."""
    return _scope.set(CallScope())


def close_plain_scope(token: contextvars.Token) -> None:
    _scope.reset(token)


def framework_initiated() -> bool:
    """Whether the current call was issued by the framework from inside another call (B362)."""
    return bool(getattr(current_scope(), "framework_initiated", False))


def run_as_framework(fn, *args, **kwargs):
    """Run fn with every tool call it makes counted as framework-initiated (the pre-mission engine deck, B273)."""
    token = _inside_call.set(True)
    try:
        return fn(*args, **kwargs)
    finally:
        _inside_call.reset(token)


class _ScopeProxy:
    """Attribute access on the CURRENT call's scope, so ``getattr(_call_ctx, "x", None)`` / ``_call_ctx.x = v`` keep
    their meaning (one call's value) without the thread-local's reuse across calls."""

    __slots__ = ()

    def __getattr__(self, name):
        return getattr(current_scope(), name)

    def __setattr__(self, name, value):
        setattr(current_scope(), name, value)

    def __delattr__(self, name):
        delattr(current_scope(), name)

    @property
    def __dict__(self):          # tests clear a call's state with _call_ctx.__dict__.clear()
        return current_scope().__dict__


call_ctx = _ScopeProxy()


def call_notes() -> dict:
    """The current call's reply notes (overridden values, kept-back settings, pinned controls), never shared."""
    scope = current_scope()
    notes = getattr(scope, "notes", None)
    if not isinstance(notes, dict):
        notes = scope.notes = {}
    return notes
