"""
TIP-001 integration tests for conversation_loop skill injection.

These tests verify:
1. Skills are injected via user message (not system prompt)
2. System prompt remains byte-stable
3. current_turn_user_idx is adjusted correctly

Run with: python3 -m pytest agent/test_tip001_integration.py -v
"""

import subprocess
import sys


def test_skill_injection_uses_user_message():
    """Verify skills inject via user message, not system prompt."""
    result = subprocess.run(
        ["grep", "-n", "s.messages.insert", "agent/conversation_loop.py"],
        capture_output=True,
        text=True,
        cwd="/Users/scottjones/.hermes/hermes-agent"
    )
    assert result.returncode == 0, "Skills should be injected via s.messages.insert"
    assert 'role": "user"' in result.stdout or "user" in result.stdout.lower(), \
        "Skill message should have user role"


def test_system_prompt_not_modified_in_skill_block():
    """Verify the skill injection block does NOT modify active_system_prompt."""
    # Read the relevant section of conversation_loop.py
    result = subprocess.run(
        ["sed", "-n", "1547,1580p", "agent/conversation_loop.py"],
        capture_output=True,
        text=True,
        cwd="/Users/scottjones/.hermes/hermes-agent"
    )
    
    skill_block = result.stdout
    
    # The old code was: s.active_system_prompt = (s.active_system_prompt or "") + "\n\n" + _skill_prompt
    # The new code should NOT have this pattern
    assert "s.active_system_prompt = (s.active_system_prompt or \"\") + " not in skill_block, \
        "System prompt should NOT be modified for skill injection"
    
    # Instead, should have message insertion
    assert "s.messages.insert" in skill_block, \
        "Skills should be injected via s.messages.insert"


def test_current_turn_user_idx_adjusted():
    """Verify current_turn_user_idx is adjusted after message insertion."""
    result = subprocess.run(
        ["grep", "-n", "current_turn_user_idx += 1", "agent/conversation_loop.py"],
        capture_output=True,
        text=True,
        cwd="/Users/scottjones/.hermes/hermes-agent"
    )
    assert result.returncode == 0, \
        "current_turn_user_idx must be adjusted after message insertion"


def test_tip001_classification_before_build_turn_context():
    """Verify TIP-001 classification runs before build_turn_context."""
    # Find the line where build_turn_context is actually called
    result = subprocess.run(
        ["grep", "-n", "_ctx = build_turn_context", "agent/conversation_loop.py"],
        capture_output=True,
        text=True,
        cwd="/Users/scottjones/.hermes/hermes-agent"
    )
    
    lines = result.stdout.strip().split('\n')
    build_turn_context_line = int(lines[0].split(':')[0]) if lines and lines[0] else 0
    
    # Find TIP-001 classification
    result2 = subprocess.run(
        ["grep", "-n", "TIP-001: Lightweight", "agent/conversation_loop.py"],
        capture_output=True,
        text=True,
        cwd="/Users/scottjones/.hermes/hermes-agent"
    )
    
    if result2.returncode == 0 and build_turn_context_line > 0:
        tip001_line = int(result2.stdout.strip().split(':')[0])
        assert tip001_line < build_turn_context_line, \
            f"Classification (line {tip001_line}) should run before build_turn_context (line {build_turn_context_line})"
    else:
        # Fallback: just check both exist
        assert result.returncode == 0, "build_turn_context call should exist"
        assert result2.returncode == 0, "TIP-001 classification should exist"


def test_syntax_check():
    """Verify conversation_loop.py has valid Python syntax."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", "agent/conversation_loop.py"],
        cwd="/Users/scottjones/.hermes/hermes-agent",
        capture_output=True
    )
    assert result.returncode == 0, f"Syntax error: {result.stderr.decode()}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
