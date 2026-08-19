from __future__ import annotations

import json
import os
from typing import Any

from pathlib import Path

from openai import OpenAI

HINTS = Path(__file__).resolve().parent / "hints" / "core-console.md"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "act",
            "description": "Perform one UI action on the current screen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["click", "fill", "select", "press", "dismiss", "wait"],
                    },
                    "by": {
                        "type": "string",
                        "enum": ["role", "label", "placeholder", "text"],
                        "description": "How to find the control. For links use by=role and role=link.",
                    },
                    "role": {
                        "type": "string",
                        "description": "textbox, button, link, heading, combobox, dialog",
                    },
                    "name": {"type": "string"},
                    "text": {"type": "string"},
                    "value": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["action", "rationale"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Goal is met. Provide extracted outputs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "outputs": {"type": "object", "additionalProperties": {"type": "string"}},
                    "rationale": {"type": "string"},
                },
                "required": ["rationale"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stuck",
            "description": "Cannot safely continue.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
]

SYSTEM = """You operate a legacy bank Core Console through accessibility roles and names.
You already signed on. Complete the user's goal with the fewest actions.

Locator rules (strict):
- `by` must be one of: role, label, placeholder, text.
- For a link, use by=role, role=link, name=exact link text (e.g. "Open sub-account").
- role must be textbox, button, heading, link, dialog, combobox — never "Search Button".
- fill Member ID before Search. Nickname is required before Continue.
- System notice dialog → click button OK.
- Never Close account; call stuck.
When the confirmation or savings figure is visible, call done with outputs.
"""


def system_prompt() -> str:
    extra = HINTS.read_text() if HINTS.exists() else ""
    return SYSTEM + "\n\n" + extra


class LLMClient:
    def __init__(self) -> None:
        provider = os.environ.get("LLM_PROVIDER", "openai").strip().lower()
        base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OLLAMA_BASE_URL")
        if provider == "ollama":
            base_url = base_url or "http://127.0.0.1:11434/v1"
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OLLAMA_API_KEY")
        if provider == "ollama":
            api_key = api_key or "ollama"
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required for discovery, or set LLM_PROVIDER=ollama"
            )
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        default_model = "llama3.2:latest" if provider == "ollama" else "gpt-4.1-mini"
        self.model = os.environ.get("OLLAMA_MODEL") or os.environ.get("OPENAI_MODEL") or default_model
        self.provider = provider

    def next_tool(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "required",
        }
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception:
            kwargs.pop("tool_choice", None)
            resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            nudge = list(messages) + [
                {
                    "role": "user",
                    "content": "You must call one tool now: act, done, or stuck. No prose.",
                }
            ]
            kwargs["messages"] = nudge
            try:
                resp = self.client.chat.completions.create(**kwargs)
                msg = resp.choices[0].message
            except Exception:
                msg = resp.choices[0].message
        if not msg.tool_calls:
            return {
                "id": "coach-fallback",
                "name": "_fallback",
                "arguments": {},
                "assistant": (msg.model_dump() if msg else {}),
            }
        call = msg.tool_calls[0]
        args = json.loads(call.function.arguments or "{}")
        if not isinstance(args, dict):
            args = {}
        return {
            "id": call.id,
            "name": call.function.name,
            "arguments": args,
            "assistant": msg.model_dump(),
        }
