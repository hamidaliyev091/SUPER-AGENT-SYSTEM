"""GenieXBridge (SAS side of the GenieX bridge contract, v2).

A stdlib-only HTTP client for the Android GenieX loopback server
(docs/implementation/GENIEX_BRIDGE.md). The interface is the standard
OpenAI-compatible chat completions contract with multimodal image content
parts - no custom protocol. Security rules enforced here:

- loopback only: a non-loopback bridge URL is refused (the bridge is a
  local IPC channel, never a remote host);
- the bridge is inference only: whatever it returns is model output and
  flows through the normal ModelPort -> proposal -> Policy path;
- failures raise GenieXBridgeError with a stable code (CONNECTION, TIMEOUT,
  HTTP, PROTOCOL, CONFIG, LOOPBACK) so callers can fail safely without
  crashing the governed loop.
"""
from __future__ import annotations

import base64
import os
from typing import List, Optional

from .bridge_http import (
    DEFAULT_BRIDGE_URL,
    BridgeError,
    loopback_host as _loopback_host_shared,
    request_json,
)


class GenieXBridgeError(BridgeError):
    """Bridge failure with a stable, machine-readable code."""


def _loopback_host(url: str) -> str:
    try:
        return _loopback_host_shared(url)
    except BridgeError as exc:
        raise GenieXBridgeError(exc.code, exc.message)


def text_message(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def image_message(prompt: str, image_bytes: bytes,
                  mime_type: str = "image/png") -> dict:
    """OpenAI-compatible multimodal user message with an image part."""
    data_url = f"data:{mime_type};base64," + base64.b64encode(image_bytes).decode("ascii")
    return {"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]}


class GenieXBridge:
    """Client for the GenieX local inference bridge (contract v2)."""

    def __init__(self, base_url: Optional[str] = None,
                 timeout: Optional[float] = None,
                 llm_model: Optional[str] = None,
                 vlm_model: Optional[str] = None):
        self.base_url = (base_url or os.environ.get("GENIEX_BRIDGE_URL")
                         or DEFAULT_BRIDGE_URL).rstrip("/")
        _loopback_host(self.base_url)  # fail closed on configuration errors
        try:
            self.timeout = float(timeout if timeout is not None
                                 else os.environ.get("GENIEX_BRIDGE_TIMEOUT",
                                                     "120"))
        except ValueError:
            raise GenieXBridgeError("CONFIG", "GENIEX_BRIDGE_TIMEOUT is not a number")
        self.llm_model = llm_model or os.environ.get(
            "GENIEX_LLM_MODEL", "qwen3-4b-instruct-2507")
        self.vlm_model = vlm_model or os.environ.get(
            "GENIEX_VLM_MODEL", "qwen2.5-vl-7b-instruct")

    # -- public API (contract v2) -------------------------------------------------

    def health(self) -> dict:
        return self._request("GET", "/v1/health")

    def chat_completions(self, model: str, messages: List[dict],
                         max_tokens: int = 2048,
                         temperature: float = 0.0) -> dict:
        """OpenAI-compatible POST /v1/chat/completions. Returns the raw
        OpenAI-shaped response dict (choices, usage, ...)."""
        return self._request("POST", "/v1/chat/completions", body={
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })

    def vision_description(self, model: str, image_bytes: bytes, prompt: str,
                           max_tokens: int = 512) -> dict:
        """VLM inference through the SAME chat completions endpoint using
        multimodal image content (no separate vision endpoint)."""
        return self.chat_completions(model, [image_message(prompt, image_bytes)],
                                     max_tokens=max_tokens)

    # -- transport ------------------------------------------------------------------

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        decoded = request_json(self.base_url, path, method=method, body=body,
                               timeout=self.timeout, error=GenieXBridgeError)
        if "error" in decoded and isinstance(decoded.get("error"), dict):
            raise GenieXBridgeError(
                "HTTP", decoded["error"].get("message", "bridge error"))
        return decoded
