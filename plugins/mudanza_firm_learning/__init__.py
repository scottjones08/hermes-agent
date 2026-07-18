"""Mudanza firm-learning observer.

The plugin emits metadata-only lifecycle signals. It never sends prompts,
responses, tool arguments, tool results, or transcript content. Inference is
separately routed through the ``mudanza`` model-provider profile.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_QUEUE: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1000)
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()


def _enabled() -> bool:
    return os.getenv("MUDANZA_FIRM_LEARNING_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _token() -> str:
    return (
        os.getenv("MUDANZA_FIRM_LEARNING_TOKEN", "").strip()
        or os.getenv("MUDANZA_ROUTER_API_KEY", "").strip()
    )


def _events_url() -> str:
    explicit = os.getenv("MUDANZA_FIRM_LEARNING_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    router = os.getenv(
        "MUDANZA_ROUTER_BASE_URL",
        "https://tessara-prod-func.azurewebsites.net/api/hermes/v1",
    ).rstrip("/")
    if router.endswith("/hermes/v1"):
        router = router[: -len("/hermes/v1")]
    return f"{router}/firm-learning/events"


def _hash(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()


def _safe_name(value: Any, maximum: int = 120) -> str:
    text = "".join(ch if ch.isalnum() or ch in "._:-" else "-" for ch in str(value or ""))
    return text.strip("-")[:maximum] or "unknown"


def _send_event(payload: dict[str, Any]) -> None:
    token = _token()
    if not token or not _enabled():
        return
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        _events_url(),
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "hermes-agent/mudanza-firm-learning",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read(1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.debug("Mudanza learning event delivery failed: %s", exc)


def _worker() -> None:
    while True:
        payload = _QUEUE.get()
        try:
            _send_event(payload)
        except Exception as exc:  # observer must never break Hermes
            logger.debug("Mudanza learning observer failed safely: %s", exc)
        finally:
            _QUEUE.task_done()


def _ensure_worker() -> None:
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return
        threading.Thread(
            target=_worker,
            name="mudanza-firm-learning",
            daemon=True,
        ).start()
        _WORKER_STARTED = True


def _emit(payload: dict[str, Any]) -> None:
    if not _enabled() or not _token():
        return
    _ensure_worker()
    payload.setdefault("observedAt", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    try:
        _QUEUE.put_nowait(payload)
    except queue.Full:
        logger.warning("Mudanza learning queue is full; dropping metadata-only event")


def on_post_llm_call(**kwargs: Any) -> None:
    user_message = kwargs.get("user_message", "")
    assistant_response = kwargs.get("assistant_response", "")
    model = _safe_name(kwargs.get("model"))
    session_id = _safe_name(kwargs.get("session_id"), 128)
    _emit({
        "sourceType": "hermes_plugin",
        "sourceId": "post_llm_call",
        "eventType": "hermes_turn_completed",
        "learningKey": f"hermes:model:{model}:turn",
        "summary": f"Hermes completed a turn with {model}.",
        "contentHash": _hash(f"{user_message}\n{assistant_response}"),
        "traceId": session_id,
        "model": model,
        "confidence": 0.65,
        "outcome": "success",
        "evidence": {
            "userCharacters": len(str(user_message or "")),
            "assistantCharacters": len(str(assistant_response or "")),
            "rawContentStored": False,
        },
    })


def on_post_tool_call(**kwargs: Any) -> None:
    tool_name = _safe_name(kwargs.get("tool_name"))
    result = kwargs.get("result", "")
    result_text = str(result or "")
    failed = any(marker in result_text.lower() for marker in ("error", "failed", "exception"))
    _emit({
        "sourceType": "hermes_plugin",
        "sourceId": "post_tool_call",
        "eventType": "tool_failure" if failed else "tool_success",
        "learningKey": f"tool:{tool_name}:{'failure' if failed else 'success'}",
        "summary": f"Hermes tool {tool_name} {'failed' if failed else 'completed'}.",
        "contentHash": _hash(result_text),
        "traceId": _safe_name(kwargs.get("task_id"), 128),
        "confidence": 0.75 if failed else 0.6,
        "outcome": "failure" if failed else "success",
        "evidence": {
            "toolName": tool_name,
            "durationMs": int(kwargs.get("duration_ms") or 0),
            "resultCharacters": len(result_text),
            "argumentsStored": False,
            "resultStored": False,
        },
    })


def on_post_approval_response(**kwargs: Any) -> None:
    choice = _safe_name(kwargs.get("choice"), 32)
    surface = _safe_name(kwargs.get("surface"), 32)
    pattern = _safe_name(kwargs.get("pattern_key"), 120)
    _emit({
        "sourceType": "hermes_plugin",
        "sourceId": "post_approval_response",
        "eventType": "approval_response",
        "learningKey": f"approval:{pattern}:{choice}",
        "summary": f"A Hermes {surface} approval request was answered {choice}.",
        "confidence": 0.8,
        "outcome": "rejected" if choice in {"deny", "timeout"} else "approved",
        "evidence": {
            "surface": surface,
            "patternKey": pattern,
            "choice": choice,
            "commandStored": False,
        },
    })


def register(ctx: Any) -> None:
    ctx.register_hook("post_llm_call", on_post_llm_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("post_approval_response", on_post_approval_response)

