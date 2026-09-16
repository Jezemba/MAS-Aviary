"""Model wrapper with think-block stripping, robust JSON parsing, and reliability patterns.

Works with any model that emits chain-of-thought inside ``<think>...</think>``
tags (Qwen3, DeepSeek-R1, etc.).  Three layers of reliability:

1. **Think-block stripping** — ``<think>...</think>`` content is removed from
   the raw model output *before* tool-call parsing runs, so braces inside
   reasoning traces no longer confuse the JSON extractor.

2. **Robust JSON extraction** — instead of "first ``{`` to last ``}``", we
   find the JSON object that actually contains the expected tool-call keys
   (``name`` and ``arguments`` by default).  This handles models that emit
   analysis text with incidental braces before or around the real tool call.

3. **Prompt+validate retry** — on parse failure inside ``generate()``, the
   error is fed back to the model and generation is retried.  Retries are
   transparent to smolagents and do NOT consume ``max_steps`` budget.
   (Ported from CMU design-research-agents ``structured_output.py``.)
"""

from __future__ import annotations

import json
import logging
import threading
import contextlib
import re
import uuid
from typing import Any

from smolagents import Tool, TransformersModel
from smolagents.models import (
    ChatMessage,
    ChatMessageToolCall,
    ChatMessageToolCallFunction,
    MessageRole,
    parse_json_if_needed,
)

from src.llm.reliability import ReliabilityConfig, add_strict_properties

logger = logging.getLogger(__name__)

# Pre-compiled pattern for <think>...</think> blocks (supports nested tags).
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# gpt-oss (harmony format) names the function in a channel header rather than
# inside the JSON body:
#
#     commentary to=functions.get_wing_area json{"aircraft_name": "DLR-F25"}
#
# i.e. the JSON carries ONLY the arguments.  The generic finder below looks for
# an object holding both "name" and "arguments", fails, and falls back to the
# first dict it saw — the bare argument object — producing
# "Tool call needs a 'name' key. Got keys: ['aircraft_name']".  That cost a
# retry on essentially EVERY gpt-oss tool call.  Recognise the header instead.
_HARMONY_TOOL_RE = re.compile(r"to\s*=\s*(?:functions?\.)([A-Za-z_][A-Za-z0-9_]*)")


def _find_tool_call_json(
    text: str,
    name_key: str = "name",
    arguments_key: str = "arguments",
) -> dict:
    """Extract the JSON object containing *name_key* and *arguments_key*.

    Strategy:
      1. Try every ``{`` in the text as a candidate start.
      2. For each candidate, use a brace-depth counter to find the matching
         closing ``}``.
      3. Attempt ``json.loads`` on the substring.
      4. If the resulting dict contains both expected keys → return it.

    Fallback (CMU DRC pattern): if brace-depth scanning fails (e.g.
    truncated or malformed JSON), use ``json.JSONDecoder().raw_decode()``
    which can recover partial JSON objects from arbitrary text.

    Raises ``ValueError`` when no valid JSON object is found at all.
    """
    candidates: list[dict] = []

    # gpt-oss / harmony: the function name lives in a `to=functions.NAME`
    # header and the JSON body holds only the arguments.  Detect that shape
    # FIRST and rebuild the canonical {name, arguments} dict, otherwise the
    # generic scan below returns the bare argument object and the caller
    # rejects it for missing a name key.  Only fires when the text does not
    # already contain a well-formed call, so models that emit the standard
    # shape (Qwen3 et al.) are completely unaffected.
    harmony = _HARMONY_TOOL_RE.search(text)
    if harmony:
        decoder = json.JSONDecoder(strict=False)
        pos = harmony.end()
        while pos < len(text):
            idx = text.find("{", pos)
            if idx == -1:
                break
            try:
                obj, end_idx = decoder.raw_decode(text, idx)
            except (json.JSONDecodeError, ValueError):
                pos = idx + 1
                continue
            if isinstance(obj, dict):
                # A complete call after the header wins — use it as-is.
                if name_key in obj and arguments_key in obj:
                    return obj
                return {name_key: harmony.group(1), arguments_key: obj}
            pos = end_idx

    i = 0
    while i < len(text):
        if text[i] != "{":
            i += 1
            continue

        # Walk forward counting brace depth.
        depth = 0
        for j in range(i, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            if depth == 0:
                candidate_str = text[i : j + 1]
                try:
                    obj = json.loads(candidate_str, strict=False)
                    if isinstance(obj, dict):
                        if name_key in obj and arguments_key in obj:
                            return obj
                        candidates.append(obj)
                except (json.JSONDecodeError, ValueError):
                    pass
                break
        i += 1

    # No object with the expected keys — return best candidate if any.
    if candidates:
        return candidates[0]

    # Fallback: raw_decode scanning (CMU DRC pattern).
    # JSONDecoder.raw_decode() can recover valid JSON objects from
    # positions where brace-depth counting failed (e.g. escaped braces,
    # strings containing braces, or partially truncated output).
    decoder = json.JSONDecoder(strict=False)
    raw_candidates: list[dict] = []
    pos = 0
    while pos < len(text):
        idx = text.find("{", pos)
        if idx == -1:
            break
        try:
            obj, end_idx = decoder.raw_decode(text, idx)
            if isinstance(obj, dict):
                if name_key in obj and arguments_key in obj:
                    return obj
                raw_candidates.append(obj)
            pos = end_idx
        except (json.JSONDecodeError, ValueError):
            pos = idx + 1

    if raw_candidates:
        return raw_candidates[0]

    raise ValueError("The model output does not contain any JSON blob.")


class ThinkingModel(TransformersModel):
    """TransformersModel subclass with think-block stripping, robust parsing,
    and CMU-style reliability patterns (retry + strict schemas).

    Drop-in replacement — accepts the same constructor arguments as
    ``TransformersModel`` plus an optional ``reliability`` config.
    """

    def __init__(self, *args, reliability: ReliabilityConfig | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._reliability = reliability or ReliabilityConfig()
        # Per-call thinking toggle.  When False, ``enable_thinking=False``
        # is injected into apply_chat_template_kwargs for the next
        # generate() call chain.  Strategies set this to False for agents
        # that need reliable JSON (e.g. orchestrator) and True for agents
        # that benefit from reasoning (e.g. simulation_executor).
        self._thinking_enabled: bool = True

    @property
    def thinking_enabled(self) -> bool:
        return self._thinking_enabled

    @thinking_enabled.setter
    def thinking_enabled(self, value: bool) -> None:
        self._thinking_enabled = value

    # B82: thinking mode is PER CALL, held in thread-local state.
    #
    # It used to be set by mutating self.apply_chat_template_kwargs and restoring
    # it afterwards. That is shared instance state, so two threads generating at
    # once could flip each other's setting -- a real race that the B80/B81
    # generation lock was accidentally hiding. Networked peers generate
    # concurrently again (B82), so the toggle has to be per call.
    #
    # apply_chat_template_kwargs (which smolagents' TransformersModel reads when it
    # builds the prompt) is therefore a read-only VIEW: the stored kwargs plus this
    # thread's thinking setting. Nothing is mutated, so no lock is needed and
    # template building stays parallel.
    _thinking_tls = threading.local()

    @property
    def apply_chat_template_kwargs(self) -> dict[str, Any]:
        stored = dict(getattr(self, "_chat_template_kwargs", None) or {})
        if self._thinking_for_call():
            stored.pop("enable_thinking", None)      # Qwen3 thinks by default
        else:
            stored["enable_thinking"] = False
        return stored

    @apply_chat_template_kwargs.setter
    def apply_chat_template_kwargs(self, value: dict[str, Any] | None) -> None:
        self._chat_template_kwargs = dict(value or {})

    def _thinking_for_call(self) -> bool:
        """Thinking setting for THIS thread's current generation."""
        value = getattr(type(self)._thinking_tls, "enabled", None)
        if value is not None:
            return bool(value)
        return bool(getattr(self, "_thinking_enabled", True))

    @contextlib.contextmanager
    def _thinking_for_this_call(self, enabled: bool):
        tls = type(self)._thinking_tls
        previous = getattr(tls, "enabled", None)
        tls.enabled = bool(enabled)
        try:
            yield
        finally:
            if previous is None:
                del tls.enabled
            else:
                tls.enabled = previous

    # -- Truncation detection ---------------------------------------------------

    def _is_truncated(self, message: ChatMessage) -> bool:
        """Return True if the output was truncated at max_new_tokens.

        Truncated outputs have exactly max_new_tokens of generated text
        and typically contain an unclosed ``<think>`` block with no JSON.
        """
        content = message.content or ""
        # Heuristic: unclosed <think> block (opened but never closed).
        has_open_think = "<think>" in content and "</think>" not in content
        if has_open_think:
            return True
        # Heuristic: no braces at all in a supposedly-tool-calling response.
        stripped = strip_think_blocks(content)
        if not stripped or ("{" not in stripped and "}" not in stripped):
            # Only consider truncation if the raw content was substantial.
            if len(content) > 200:
                return True
        return False

    # -- Retry-aware generate --------------------------------------------------

    def generate(
        self,
        messages: list[ChatMessage | dict],
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from: list[Tool] | None = None,
        **kwargs: Any,
    ) -> ChatMessage:
        """Generate with prompt+validate retry on parse failure.

        On each attempt: call ``super().generate()`` then
        ``self.parse_tool_calls()``.  If parsing fails, append the
        failed output and an error-feedback message to the conversation
        and retry.  Retries happen *inside* ``generate()`` so smolagents
        sees only one step regardless of retry count.

        Truncation detection (Fix 3): when the output is truncated
        (unclosed ``<think>`` block, no JSON), the retry disables
        thinking mode for that specific attempt instead of appending
        more context.
        """
        # B83 (Jessica, 2026-09-16): while peers are dispatched, generations go through
        # the ONE batching worker -- concurrent generate() on the accelerate-sharded 32B
        # crashes with "CUDA error: invalid argument", so B82's slots let peers overlap
        # straight into that crash. The worker is the only thread that touches the model;
        # peers still all sit inside generate() at once and are served by one forward pass.
        # Off the networked path the worker is inactive and this is the B82 behaviour.
        from src.llm.batch_generation import is_active

        if is_active():
            return self._generate_in_slot(messages, stop_sequences, response_format,
                                          tools_to_call_from, **kwargs)
        from src.llm.generation_slots import generation_slot

        with generation_slot():
            return self._generate_in_slot(messages, stop_sequences, response_format,
                                          tools_to_call_from, **kwargs)

    def _generate_in_slot(self, messages, stop_sequences, response_format, tools_to_call_from, **kwargs):
        max_retries = self._reliability.max_retries
        last_error: Exception | None = None
        # Work on a copy so retries don't pollute the caller's list.
        msgs = list(messages)

        # Apply the per-call thinking toggle (Fix 2; per thread since B82).
        thinking = self._thinking_enabled
        with self._thinking_for_this_call(thinking):
            for attempt in range(max_retries + 1):
                raw_message = self._generate_once(
                    msgs,
                    stop_sequences=stop_sequences,
                    response_format=response_format,
                    tools_to_call_from=tools_to_call_from,
                    **kwargs,
                )
                try:
                    parsed = self.parse_tool_calls(raw_message)
                    if attempt > 0:
                        logger.info("Retry %d/%d succeeded", attempt, max_retries)
                    return parsed
                except (ValueError, AssertionError) as exc:
                    last_error = exc
                    if attempt == max_retries:
                        break

                    # Fix 3: truncation detection — if the output was
                    # truncated by max_new_tokens (unclosed <think> block,
                    # no JSON), disable thinking for the retry instead of
                    # appending more feedback context.
                    truncated = self._is_truncated(raw_message)
                    if truncated:
                        logger.warning(
                            "Output truncated (attempt %d/%d) — disabling thinking for retry",
                            attempt + 1,
                            max_retries + 1,
                        )
                        type(self)._thinking_tls.enabled = False
                        # Don't append error feedback — the model just
                        # needs more token budget for the actual JSON.
                        continue

                    logger.warning(
                        "Parse failed (attempt %d/%d): %s — retrying",
                        attempt + 1,
                        max_retries + 1,
                        exc,
                    )
                    # Append the failed assistant output and error feedback
                    # so the model can self-correct on the next attempt.
                    # Use content-block format ([{"type":"text","text":...}])
                    # to match smolagents' internal message representation —
                    # plain strings cause "string indices must be integers"
                    # when get_clean_message_list merges consecutive same-role
                    # messages.
                    error_feedback = (
                        "Your previous response could not be parsed as a "
                        f"valid tool call. Error: {exc}\n"
                        "Please respond with ONLY a valid JSON tool call "
                        "in the format: "
                        '{{"name": "<tool_name>", "arguments": {{...}}}}'
                    )
                    msgs = msgs + [
                        {
                            "role": "assistant",
                            "content": [{"type": "text", "text": raw_message.content or ""}],
                        },
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": error_feedback}],
                        },
                    ]

        # All retries exhausted.  Instead of raising (which smolagents
        # wraps as AgentGenerationError — a fatal error that kills run()),
        # return the raw message without tool_calls.  smolagents' own
        # _step_stream will then call parse_tool_calls() on our returned
        # message, get AgentParsingError (non-fatal), log it on the step,
        # and let the model self-correct on the next step.  This preserves
        # the natural error-recovery loop (CMU DRC "structured failure as
        # data" pattern) instead of escalating to an unrecoverable crash.
        logger.warning(
            "All %d retries exhausted: %s — returning raw message for smolagents error-recovery loop",
            max_retries + 1,
            last_error,
        )
        return raw_message  # type: ignore[possibly-undefined]

    def _generate_once(self, msgs, stop_sequences=None, response_format=None,
                       tools_to_call_from=None, **kwargs):
        """One underlying generation: through the batch worker when peers are dispatched.

        The worker builds the prompt too (B80 budget included), so the thinking setting
        for THIS call has to travel with the request -- it is thread-local (B82) and the
        worker is a different thread.
        """
        from src.llm.batch_generation import is_active, submit

        if is_active():
            if response_format is not None:
                # Same refusal as TransformersModel: batching must not silently drop it.
                raise ValueError("Transformers does not support structured outputs, use VLLMModel for this.")
            return submit(self, msgs, stop_sequences=stop_sequences,
                          tools_to_call_from=tools_to_call_from,
                          thinking=self._thinking_for_call(), **kwargs)
        return super().generate(
            msgs,
            stop_sequences=stop_sequences,
            response_format=response_format,
            tools_to_call_from=tools_to_call_from,
            **kwargs,
        )

    # -- B80 prompt budget -----------------------------------------------------

    def _prepare_completion_args(self, messages, stop_sequences=None, tools_to_call_from=None, **kwargs):
        """Build the generation inputs, trimmed to the prompt budget first (B80).

        Tokens are counted exactly as they will be generated -- this model's own chat
        template, including the tool schemas -- and when the prompt exceeds
        PROMPT_BUDGET_TOKENS the oldest steps are replaced by a knowledge-base summary
        (src/llm/context_budget.py). Trimming happens before generation, never after an
        OOM, and the agent's own memory is not modified.
        """
        from src.llm.context_budget import HARD_PROMPT_LIMIT_TOKENS, fit_to_budget
        from src.tools.knowledge_base import PROMPT_BUDGET_TOKENS, get_kb

        base = super()._prepare_completion_args
        built: list[tuple[list, dict]] = []   # keeps every candidate alive for the identity match

        def count(msgs: list) -> int:
            # apply_chat_template_kwargs is a per-thread view (see above), so the
            # base can build this thread's prompt while other peers build theirs.
            args = base(msgs, stop_sequences=stop_sequences,
                        tools_to_call_from=tools_to_call_from, **kwargs)
            built.append((msgs, args))
            return int(args["inputs"].shape[1])

        fitted, tokens = fit_to_budget(list(messages), count, PROMPT_BUDGET_TOKENS)
        # B82: the budget must BIND. validate4 run 9 trimmed 78 times and still sent
        # 45,707 tokens, above the model's 40,960 maximum, because dropping whole
        # steps cannot help when one message is itself enormous.
        if tokens > PROMPT_BUDGET_TOKENS:
            fitted, tokens = fit_to_budget(fitted, count, PROMPT_BUDGET_TOKENS, truncate_messages=True)
        if tokens > HARD_PROMPT_LIMIT_TOKENS:
            kb = get_kb()
            if kb is not None:
                kb.bump("prompt_over_limit_count")
            raise ValueError(
                f"prompt is {tokens} tokens, above the model maximum of {HARD_PROMPT_LIMIT_TOKENS} "
                "even after trimming and message truncation (B82)"
            )
        for msgs, args in reversed(built):
            if msgs is fitted:
                return args
        return base(fitted, stop_sequences=stop_sequences, tools_to_call_from=tools_to_call_from, **kwargs)

    # -- Strict tool schemas ---------------------------------------------------

    def _prepare_completion_kwargs(
        self,
        messages: list[ChatMessage | dict],
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from: list[Tool] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Add ``additionalProperties: false`` to tool parameter schemas."""
        result = super()._prepare_completion_kwargs(
            messages=messages,
            stop_sequences=stop_sequences,
            response_format=response_format,
            tools_to_call_from=tools_to_call_from,
            **kwargs,
        )
        if self._reliability.strict_tool_schemas and "tools" in result:
            add_strict_properties(result["tools"])
        return result

    # -- Think-block-aware parse -----------------------------------------------

    def parse_tool_calls(self, message: ChatMessage) -> ChatMessage:
        """Strip ``<think>`` blocks, then parse tool calls with a robust JSON finder."""
        message.role = MessageRole.ASSISTANT

        if not message.tool_calls:
            assert message.content is not None, "Message contains no content and no tool calls"

            # 1. Strip <think>...</think> blocks.
            cleaned = strip_think_blocks(message.content)

            # 2. Extract the tool-call JSON using the robust parser.
            tool_dict = _find_tool_call_json(
                cleaned,
                name_key=self.tool_name_key,
                arguments_key=self.tool_arguments_key,
            )

            tool_name = tool_dict.get(self.tool_name_key)
            if tool_name is None:
                raise ValueError(f"Tool call needs a '{self.tool_name_key}' key. Got keys: {list(tool_dict.keys())}")

            tool_arguments = tool_dict.get(self.tool_arguments_key)
            if isinstance(tool_arguments, str):
                tool_arguments = parse_json_if_needed(tool_arguments)

            message.tool_calls = [
                ChatMessageToolCall(
                    id=str(uuid.uuid4()),
                    type="function",
                    function=ChatMessageToolCallFunction(name=tool_name, arguments=tool_arguments),
                )
            ]

            # Update content to the cleaned version so downstream code
            # (prompt templates, logging) sees tidy output.
            message.content = cleaned

        assert len(message.tool_calls) > 0, "No tool call was found in the model output"

        for tool_call in message.tool_calls:
            tool_call.function.arguments = parse_json_if_needed(tool_call.function.arguments)
        return message


def strip_think_blocks(text: str) -> str:
    """Remove all ``<think>...</think>`` blocks from *text*, after RECORDING them.

    Stripping is necessary -- reasoning traces confuse the JSON extractor -- but
    discarding them left the logs with tool calls and observations and no WHY.
    Debugging a run that calls run_simulation over and over then means inferring
    motive from behaviour, which is guessing: an agent exploring a design space
    and an agent stuck in a loop look identical from the outside.

    So the reasoning is emitted to the logger before it is dropped. It reaches
    the sweep log (and from there the W&B console mirror) without re-entering the
    model's context, so it changes nothing the agent sees -- purely an
    observability change, not a behavioural one.
    """
    blocks = _THINK_RE.findall(text or "")
    for block in blocks:
        inner = block[len("<think>"):-len("</think>")].strip()
        if inner:
            _log_reasoning(inner)
    return _THINK_RE.sub("", text).strip()


# Reasoning can be long and repetitive; a bounded excerpt keeps the sweep log
# readable while still answering "what did it think it was doing?".
_REASONING_CHARS = 700


def _log_reasoning(text: str) -> None:
    """Print, do not logger.info.

    The batch runner configures logging at WARNING, so an INFO record is dropped
    and the reasoning would be as invisible as before -- the same trap that made
    two earlier monitor patterns match nothing. stdout is how agent output
    already reaches the sweep log and, from there, the W&B console mirror.
    """
    excerpt = text if len(text) <= _REASONING_CHARS else text[:_REASONING_CHARS] + " ...[truncated]"
    try:
        print(f"REASONING: {excerpt}", flush=True)
    except Exception:
        pass   # never let observability break a run
