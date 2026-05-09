"""AskUserQuestionTool — let the agent pose a clarifying question mid-run.

Modeled after claude-code-src `tools/AskUserQuestionTool/`. We don't block
the run waiting for an answer (that would require a duplex channel and
deadlock detection). Instead the tool:

1. validates the question shape
2. emits a :class:`UserQuestionAsked` runtime event so the frontend can
   render a prompt block
3. returns immediately with a placeholder string so the model can decide
   whether to keep working with what it has or stop and wait

The user's answer arrives as the next user message in a follow-up turn —
the conversation_id keeps history threaded. This pairs naturally with the
existing SSE + per-conversation lock manager: the next ``/api/runs/stream``
call replaces the answer's place in the conversation, and the agent picks
up where it left off.
"""

from __future__ import annotations

import inspect
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from agentkit.runtime.events import RuntimeEvent, UserQuestionAsked
from agentkit.tools.base import Tool

logger = logging.getLogger(__name__)


ASK_USER_QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": (
                "The clarifying question to show the user. Be specific — say "
                "what's ambiguous, not 'what do you want?'."
            ),
        },
        "options": {
            "type": "array",
            "description": (
                "Optional pre-baked answer choices. The frontend can render "
                "these as buttons; the user is not required to pick one."
            ),
            "items": {"type": "string"},
        },
        "multiple": {
            "type": "boolean",
            "description": (
                "If true, the user may select more than one option. Default "
                "false (single choice or free-form answer)."
            ),
        },
    },
    "required": ["question"],
}


EmitFn = Callable[[RuntimeEvent], Awaitable[None] | None]


class AskUserQuestionTool(Tool):
    name = "AskUserQuestion"
    description = (
        "Ask the user a clarifying question when requirements are ambiguous. "
        "Use sparingly — only when continuing without an answer would waste "
        "significant work. The user's response arrives as the next user "
        "message; you'll pick up from there in the next turn."
    )
    schema = ASK_USER_QUESTION_SCHEMA
    timeout_seconds = 5.0
    max_result_chars = 1000
    result_summary_strategy = "none"

    MAX_QUESTION_CHARS = 2000
    MAX_OPTIONS = 10
    MAX_OPTION_CHARS = 200

    def __init__(
        self,
        *,
        emit: EmitFn | None = None,
        run_id: str = "",
        turn_id: str = "",
    ) -> None:
        self._emit = emit
        self._run_id = run_id
        self._turn_id = turn_id

    async def run(self, **kwargs: Any) -> str:
        question = kwargs.get("question")
        if not isinstance(question, str) or not question.strip():
            return "Error: 'question' must be a non-empty string."
        if len(question) > self.MAX_QUESTION_CHARS:
            return (
                f"Error: question is too long ({len(question)} chars; "
                f"max {self.MAX_QUESTION_CHARS})."
            )

        raw_options = kwargs.get("options") or []
        if not isinstance(raw_options, list):
            return "Error: 'options' must be an array of strings."
        if len(raw_options) > self.MAX_OPTIONS:
            return (
                f"Error: too many options ({len(raw_options)}; "
                f"max {self.MAX_OPTIONS})."
            )
        options: list[str] = []
        for index, opt in enumerate(raw_options):
            if not isinstance(opt, str):
                return f"Error: options[{index}] must be a string."
            if not opt.strip():
                return f"Error: options[{index}] must not be empty."
            if len(opt) > self.MAX_OPTION_CHARS:
                return (
                    f"Error: options[{index}] is too long "
                    f"({len(opt)} chars; max {self.MAX_OPTION_CHARS})."
                )
            options.append(opt.strip())
        multiple = bool(kwargs.get("multiple", False))

        question_id = f"q_{uuid.uuid4().hex[:10]}"

        if self._emit is not None:
            event = UserQuestionAsked(
                run_id=self._run_id,
                turn_id=self._turn_id,
                question_id=question_id,
                question=question.strip(),
                options=options,
                multiple=multiple,
            )
            result = self._emit(event)
            if inspect.isawaitable(result):
                await result

        logger.info(
            "ask_user_question_tool_invoked qid=%s options=%d multiple=%s",
            question_id, len(options), multiple,
        )

        # The placeholder result tells the model the question was sent; the
        # actual answer arrives in a subsequent turn as a user message.
        suffix = ""
        if options:
            opt_lines = "\n".join(f"  - {opt}" for opt in options)
            suffix = f"\nOffered options:\n{opt_lines}"
        return (
            f"Question delivered to the user (id={question_id}). "
            "Wait for the user's reply in the next turn before continuing on "
            "ambiguous parts of the task."
            + suffix
        )
