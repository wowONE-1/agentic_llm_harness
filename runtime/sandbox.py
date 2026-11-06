"""Commands run here, never in the gateway. One session at a time in v0."""
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from runtime.http import APIError, server


class Sandbox:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.session = None
        self.lock = threading.RLock()
        self.process = None
        self.cancelled = False
        self.results = {}
        self.busy = False

    def path(self, name):
        if not isinstance(name, str) or Path(name).is_absolute():
            raise ValueError("relative path required")
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("path outside workdir")
        if any(p in {".git", ".runtime"} for p in path.relative_to(self.root).parts):
            raise ValueError("protected path")
        return path

    def dispatch(self, method, path, data):
        if method == "GET" and path == "/health":
            return {"ok": True, "version": "v0"}
        if method != "POST" or not path.startswith("/v0/"):
            raise APIError(404, "unknown operation")
        operation = path.rsplit("/", 1)[-1]
        sid = data["session_id"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", sid):
            raise ValueError("session id")
        with self.lock:
            if operation == "provision":
                if self.session not in (None, sid):
                    raise APIError(409, "v0 supports one session")
                if self.session is None:
                    self.session, self.cancelled = sid, False
                    self.results.clear()
                return {"session_id": sid, "workdir": "/work"}
            if self.session != sid:
                raise APIError(404, "session missing")
            if operation == "resume":
                if self.busy:
                    raise APIError(409, "operation in progress")
                self.cancelled = False
                return {"resumed": True}
            if operation == "cancel":
                self.cancelled = True
                self.kill()
                return {"cancelled": True}
            if operation == "release":
                if self.busy:
                    raise APIError(409, "cancel and await result before release")
                for child in self.root.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                self.session = None
                self.results.clear()
                return {"released": True}
            if operation == "collect":
                return {"files": sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file())}
            if operation != "exec":
                raise APIError(404, "unknown operation")
            key = data["operation_id"]
            if key in self.results:
                old_data, result = self.results[key]
                if old_data != data:
                    raise APIError(409, "operation id reused with different payload")
                return result
            if self.busy:
                raise APIError(409, "operation in progress")
            self.busy = True
        start = time.monotonic()
        result = {"type": "result", "operation_id": key, "status": "ok"}
        try:
            timeout = min(30, max(0.1, float(data.get("timeout", 10))))
            tool, args = data["tool"], data["arguments"]
            required = {"ReadFile": {"path"}, "WriteFile": {"path", "content"},
                        "RunCommand": {"command"}}.get(tool)
            if required is None or not isinstance(args, dict) or set(args) != required:
                raise ValueError("invalid tool arguments")
            if any(not isinstance(value, str) for value in args.values()):
                raise ValueError("string arguments required")
            if self.cancelled:
                result.update(status="cancelled")
            elif tool == "ReadFile":
                with self.path(args["path"]).open("rb") as f:
                    raw = f.read(32769)
                result.update(output=raw[:32768].decode("utf-8", errors="replace"), truncated=len(raw) > 32768)
            elif tool == "WriteFile":
                content = args["content"].encode("utf-8")
                if len(content) > 262144:
                    raise ValueError("file too large")
                target = self.path(args["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                result["output"] = "written"
            elif tool == "RunCommand":
                # Output goes to bounded tmpfs in Docker, not unbounded PIPE memory.
                with tempfile.TemporaryFile() as output:
                    with self.lock:
                        if self.cancelled:
                            result["status"] = "cancelled"
                            return result
                        self.process = subprocess.Popen(
                            args["command"], shell=True, cwd=self.root,
                            stdout=output, stderr=subprocess.STDOUT,
                            start_new_session=True,
                            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8"},
                        )
                    try:
                        code = self.process.wait(timeout=timeout)
                        result.update(exit_code=code, status="ok" if code == 0 else "command_error")
                    except subprocess.TimeoutExpired:
                        result["status"] = "timeout"
                    finally:
                        with self.lock:
                            self.kill()  # Also kill background descendants in the process group.
                            self.process.wait(timeout=5)
                            self.process = None
                    output.seek(0)
                    raw = output.read(32769)
                    result.update(output=raw[:32768].decode(errors="replace"), truncated=len(raw) > 32768)
                    if self.cancelled:
                        result["status"] = "cancelled"
            else:
                raise ValueError("unknown tool")
        except (OSError, ValueError, KeyError, TypeError) as e:
            result.update(status="sandbox_error", error=type(e).__name__)
        finally:
            result["duration_ms"] = round((time.monotonic() - start) * 1000)
            with self.lock:
                self.results[key] = (data.copy(), result)
                self.busy = False
        return result

    def kill(self):
        if self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


if __name__ == "__main__":
    sandbox = Sandbox(os.getenv("WORK_ROOT", "/work"))
    server(("0.0.0.0", 8001), sandbox.dispatch, os.environ["SANDBOX_TOKEN"]).serve_forever()
