"""Run fixtures through the public gateway protocol; never score mock as LLM quality."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import time
import uuid

from runtime.client import Client


def run_task(client, directory, mode):
    task = json.loads((directory / "task.yaml").read_text())  # JSON is valid YAML 1.2.
    config = client.call("connect")
    if (mode == "smoke") != (config["backend"] == "mock"):
        raise ValueError("smoke requires mock; quality requires a real LLM backend")
    start = time.monotonic()
    client.connect()
    try:
        for source in sorted((directory / "fixture").rglob("*")):
            if source.is_file():
                result = client.tool("WriteFile", path=str(source.relative_to(directory / "fixture")),
                                     content=source.read_text())
                if result["status"] != "ok":
                    raise RuntimeError(f"fixture upload: {result['status']}")
        prompt = task["prompt"] if mode == "quality" else "MOCK " + json.dumps({"calls": task["smoke_calls"]})
        rid = client.call("message", request_id=uuid.uuid4().hex, prompt=prompt)["run_id"]
        events = []
        final = client.watch(rid, timeout=task["timeout"], emit=lambda raw: events.append(json.loads(raw)))
        spec = importlib.util.spec_from_file_location("checker", directory / "check.py")
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        checked_files = {}
        def read(path):
            result = client.tool("ReadFile", path=path)
            checked_files[path] = result
            if result["status"] != "ok" or result.get("truncated"):
                raise AssertionError(f"cannot read complete {path}")
            return result["output"]
        try:
            passed = final["status"] == "ok" and bool(checker.check(read))
        except (AssertionError, ValueError, KeyError):
            passed = False
        return dict(task=task["id"], mode=mode, configuration=config, passed=passed, status=final["status"],
                    failure=None if passed else {"answer": final["answer"], "files": checked_files},
                    duration_ms=round((time.monotonic() - start) * 1000),
                    usage=[e["usage"] for e in events if e["type"] == "llm"])
    finally:
        # If watch failed while a run is active, cancel and wait before deleting workdir.
        try:
            client.call("release")
        except Exception:
            client.call("cancel")
            client.watch(locals().get("rid"), timeout=90, emit=lambda _: None)
            client.call("release")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--token", default=os.getenv("GATEWAY_TOKEN", "dev-gateway-change-me"))
    parser.add_argument("--mode", choices=["smoke", "quality"], default="smoke")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", default="results/baf.jsonl")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    success = True
    with output.open("w") as stream:
        for repeat in range(args.repeat):
            for directory in sorted(Path(__file__).parent.glob("B*/*")):
                client = Client(args.url, args.token, "baf-" + uuid.uuid4().hex)
                result = run_task(client, directory, args.mode)
                result["repeat"] = repeat + 1
                stream.write(json.dumps(result) + "\n")
                stream.flush()
                print(json.dumps(result), flush=True)
                success &= result["passed"]
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
