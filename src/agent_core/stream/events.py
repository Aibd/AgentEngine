from enum import Enum


class AgentEventType(str, Enum):
    TEXT = "text"
    TASK = "task"
    TOOL_THOUGHT = "tool_thought"
    TOOL_RESULT = "tool_result"
    SEARCH_RESULT = "search_result"
    RESULT = "result"
    FINAL_RESULT = "final_result"
    ERROR = "error"
