from __future__ import annotations

import tempfile
from pathlib import Path

from agentengine.prompts.loader import PromptLoader


class TestPromptLoader:
    def test_load_yaml(self):
        content = """
version: 1
system: You are a test assistant.
next_step: What next?
templates:
  greeting: "Hello, {name}!"
"""
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "test_agent.yaml").write_text(content, encoding="utf-8")
            loader = PromptLoader(td)
            data = loader.load("test_agent")
            assert data["system"] == "You are a test assistant."
            assert data["templates"]["greeting"] == "Hello, {name}!"

    def test_load_with_explicit_extension(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "x.yaml").write_text("system: hi", encoding="utf-8")
            loader = PromptLoader(td)
            assert loader.get_system_prompt("x.yaml") == "hi"

    def test_get_system_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "helper.yaml").write_text("system: You are helpful.", encoding="utf-8")
            loader = PromptLoader(td)
            assert loader.get_system_prompt("helper") == "You are helpful."

    def test_get_template(self):
        content = """
templates:
  greet: "hi {name}"
"""
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "t.yaml").write_text(content, encoding="utf-8")
            loader = PromptLoader(td)
            assert loader.get_template("t", "greet") == "hi {name}"

    def test_load_nonexistent_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            loader = PromptLoader(td)
            assert loader.load("missing") == {}
            assert loader.get_system_prompt("missing") == ""

    def test_cache_hit(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "c.yaml").write_text("system: cached", encoding="utf-8")
            loader = PromptLoader(td)
            loader.load("c")
            assert "c" in loader._cache

    def test_refresh_bypasses_cache(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "agent.yaml"
            path.write_text("system: v1", encoding="utf-8")
            loader = PromptLoader(td)
            assert loader.get_system_prompt("agent") == "v1"

            path.write_text("system: v2", encoding="utf-8")
            assert loader.get_system_prompt("agent") == "v1"  # cached
            assert loader.load("agent", refresh=True)["system"] == "v2"
