"""One session, one active run. Durable JSONL events, polling transport v0."""
import json
import os
from pathlib import Path
import threading
import time
import uuid
from urllib.error import HTTPError, URLError

from runtime import harness
from runtime.http import APIError, request, server


class Gateway:
    def __init__(self, execute, trace_root):
        self.execute = execute
        self.trace_root = Path(trace_root)
        self.trace_root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.session = None
        self.turns = []
        self.events = []
        self.runs = {}
        self.active = None
        self.cancelled = threading.Event()

    def emit(self, kind, **fields):
        with self.lock:
            event = dict(seq=len(self.events) + 1, trace_id=self.session,
                         run_id=self.active, time=time.time(), type=kind, **fields)
            # Tool outputs may contain user data: retain metadata only in traces.
            if kind == "tool_result":
                event["result"] = {k: v for k, v in fields["result"].items()
                                   if k not in {"output", "error"}}
            with (self.trace_root / f"{self.session}.jsonl").open("a") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.events.append(event)

    def worker(self, prompt):
        try:
            status, answer = harness.run(
                self.turns, prompt,
                lambda tool, args, op: self.execute("exec", dict(
                    session_id=self.session, operation_id=op, tool=tool,
                    arguments=args, timeout=10)),
                self.emit, self.cancelled)
        except HTTPError as error:
            status, answer = "transport_error", f"Upstream HTTP {error.code}; no automatic command retry"
            error.close()
        except (URLError, TimeoutError, ConnectionError):
            status, answer = "transport_error", "Transport failed; no automatic command retry"
        except Exception:
            status, answer = "error", "Run failed; check server configuration"
        with self.lock:
            self.emit("result", status=status, answer=answer)
            self.active = None

    def dispatch(self, method, path, data):
        if method == "GET" and path == "/health":
            return {"ok": True, "version": "v0"}
        if method != "POST":
            raise APIError(404, "unknown operation")
        with self.lock:
            if path == "/v0/connect":
                return {"version": "v0", "session_model": "single",
                        "backend": os.getenv("LLM_BACKEND", "mock"),
                        "model": os.getenv("LLM_MODEL", ""),
                        "memory_turns": int(os.getenv("MEMORY_TURNS", "2"))}
            if path == "/v0/session":
                sid = data["session_id"]
                if self.session is not None and self.session != sid:
                    raise APIError(409, "one session supported")
                result = self.execute("provision", {"session_id": sid})
                if self.session is None:
                    self.session = sid
                    self.turns, self.events, self.runs = [], [], {}
                return result
            if data.get("session_id") != self.session or self.session is None:
                raise APIError(404, "session missing")
            if path == "/v0/events":
                after = int(data.get("after", 0))
                if after < 0:
                    raise ValueError("invalid cursor")
                return {"events": [e for e in self.events if e["seq"] > after]}
            if path == "/v0/message":
                rid, prompt = data["request_id"], data["prompt"]
                if not isinstance(rid, str) or not isinstance(prompt, str) or not rid or len(prompt) > 65536:
                    raise ValueError("invalid message")
                if rid in self.runs:
                    old_prompt, run_id = self.runs[rid]
                    if old_prompt != prompt:
                        raise APIError(409, "request id reused")
                    return {"run_id": run_id}
                if self.active:
                    raise APIError(409, "run active")
                self.execute("resume", {"session_id": self.session})
                self.active = uuid.uuid4().hex
                self.runs[rid] = (prompt, self.active)
                self.cancelled.clear()
                run_id = self.active
                self.emit("run_start", backend=os.getenv("LLM_BACKEND", "mock"),
                          model=os.getenv("LLM_MODEL", ""),
                          memory_turns=int(os.getenv("MEMORY_TURNS", "2")))
                threading.Thread(target=self.worker, args=(prompt,), daemon=True).start()
                return {"run_id": run_id}
            if path == "/v0/cancel":
                self.cancelled.set()
                return self.execute("cancel", {"session_id": self.session})
            if path == "/v0/release":
                if self.active:
                    raise APIError(409, "cancel and wait for result before release")
                result = self.execute("release", {"session_id": self.session})
                self.session = None
                self.turns, self.events, self.runs = [], [], {}
                return result
            if path == "/v0/tool":
                if self.active:
                    raise APIError(409, "run active")
                return self.execute("exec", data)
            raise APIError(404, "unknown operation")


def main():
    def execute(operation, data):
        return request(os.getenv("SANDBOX_URL", "http://sandbox:8001"),
                       f"/v0/{operation}", data, os.environ["SANDBOX_TOKEN"], timeout=40)

    gateway = Gateway(execute, os.getenv("TRACE_ROOT", "/traces"))
    server(("0.0.0.0", 8000), gateway.dispatch, os.environ["GATEWAY_TOKEN"]).serve_forever()


if __name__ == "__main__":
    main()
