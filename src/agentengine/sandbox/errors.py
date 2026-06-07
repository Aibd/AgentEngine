"""Sandbox error types."""

from __future__ import annotations


class SandboxError(Exception):
    """Base class for all sandbox failures."""


class SandboxStartupError(SandboxError):
    """Container started but the in-container daemon/socket never came up."""


class SandboxCapacityError(SandboxError):
    """The manager is at its configured max concurrent-container limit."""
