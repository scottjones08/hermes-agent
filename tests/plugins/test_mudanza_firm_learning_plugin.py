"""Contract tests for the metadata-only Mudanza learning observer."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch


PLUGIN_PATH = Path(__file__).parents[2] / "plugins" / "mudanza_firm_learning" / "__init__.py"


def _load_plugin():
    spec = importlib.util.spec_from_file_location("test_mudanza_firm_learning", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_registers_only_observer_hooks():
    module = _load_plugin()
    ctx = Mock()
    module.register(ctx)
    assert [call.args[0] for call in ctx.register_hook.call_args_list] == [
        "post_llm_call", "post_tool_call", "post_approval_response"
    ]


def test_llm_event_contains_hashes_and_lengths_not_content():
    module = _load_plugin()
    with patch.object(module, "_emit") as emit:
        module.on_post_llm_call(
            session_id="session-1",
            model="mudanza-auto",
            user_message="Client Jane Doe needs a review",
            assistant_response="I prepared the review",
        )
    payload = emit.call_args.args[0]
    serialized = str(payload)
    assert "Jane Doe" not in serialized
    assert "prepared the review" not in serialized
    assert len(payload["contentHash"]) == 64
    assert payload["evidence"]["rawContentStored"] is False


def test_tool_event_never_sends_arguments_or_results():
    module = _load_plugin()
    with patch.object(module, "_emit") as emit:
        module.on_post_tool_call(
            tool_name="terminal",
            args={"command": "secret"},
            result="Error: account 1234567890123456",
            duration_ms=42,
        )
    payload = emit.call_args.args[0]
    serialized = str(payload)
    assert "1234567890123456" not in serialized
    assert "secret" not in serialized
    assert payload["eventType"] == "tool_failure"
    assert payload["evidence"]["argumentsStored"] is False

