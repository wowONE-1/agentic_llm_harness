"""The LLM proxy is a module in the gateway for checkpoint one."""
import json
import os
from runtime.http import request


TOOLS = [
    {"type": "function", "function": {"name": name, "description": description,
     "parameters": {"type": "object", "properties": {key: {"type": "string"} for key in keys},
                    "required": keys, "additionalProperties": False}}}
    for name, description, keys in [
        ("ReadFile", "Read a UTF-8 file, relative to workdir", ["path"]),
        ("WriteFile", "Write a UTF-8 file, relative to workdir", ["path", "content"]),
        ("RunCommand", "Run a shell command in workdir; inspect exit_code", ["command"]),
    ]
]


def complete(messages):
    backend = os.getenv("LLM_BACKEND", "mock")
    if backend == "mock":
        # A transparent scripted double for infrastructure tests, not a quality score.
        last = messages[-1]
        if last["role"] == "tool":
            return {"role": "assistant", "content": last["content"]}, {}
        prompt = last["content"]
        if prompt.startswith("MOCK "):
            spec = json.loads(prompt[5:])
            calls = spec.get("calls", [spec])
            return {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"mock-call-{i}", "type": "function", "function": {
                    "name": call["tool"], "arguments": json.dumps(call["arguments"])}}
                for i, call in enumerate(calls)]}, {}
        return {"role": "assistant", "content": "Mock ready. Use the BAF runner or configure OpenRouter."}, {}
    if backend != "openrouter":
        raise ValueError("unknown LLM_BACKEND")
    response = request("https://openrouter.ai/api/v1", "/chat/completions", {
        "model": os.environ["LLM_MODEL"], "messages": messages, "tools": TOOLS,
        "temperature": 0, "max_tokens": 2048,
        "provider": {"require_parameters": True},
    }, token=os.environ["OPENROUTER_API_KEY"], timeout=60)
    choice = response["choices"][0]
    if choice.get("finish_reason") not in ("stop", "tool_calls"):
        raise ValueError("incomplete LLM response")
    msg = choice["message"]
    return {k: msg[k] for k in ("role", "content", "tool_calls") if k in msg}, response.get("usage", {})
