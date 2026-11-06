"""Headless checkpoint-one client. Reconnect uses session id and event cursor."""
import argparse
import json
import os
import time
import uuid
from urllib.error import HTTPError, URLError

from runtime.http import request


class Client:
    def __init__(self, url, token, session):
        self.url, self.token, self.session = url.rstrip("/"), token, session

    def call(self, operation, **fields):
        return request(self.url, f"/v0/{operation}",
                       dict(session_id=self.session, **fields), self.token, timeout=45)

    def connect(self):
        self.call("connect")
        return self.call("session")

    def tool(self, tool, **arguments):
        return self.call("tool", operation_id=uuid.uuid4().hex,
                         tool=tool, arguments=arguments, timeout=10)

    def watch(self, run_id, after=0, timeout=240, emit=print):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                events = self.call("events", after=after)["events"]
            except HTTPError:
                raise
            except (URLError, TimeoutError):
                time.sleep(0.5)  # Polling is read-only: safe to retry after an outage.
                continue
            for event in events:
                after = event["seq"]
                if run_id is None or event["run_id"] == run_id:
                    emit(json.dumps(event, ensure_ascii=False))
                    if event["type"] == "result":
                        return event
            time.sleep(0.1)
        raise TimeoutError("watch timed out; reconnect with events, or cancel")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--token", default=os.getenv("GATEWAY_TOKEN", "dev-gateway-change-me"))
    parser.add_argument("--session", default="demo")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("connect")
    chat = sub.add_parser("chat")
    chat.add_argument("prompt")
    chat.add_argument("--request-id", default=None)
    events = sub.add_parser("events")
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--run-id")
    sub.add_parser("cancel")
    sub.add_parser("release")
    args = parser.parse_args()
    client = Client(args.url, args.token, args.session)
    if args.command == "connect":
        print(json.dumps(client.connect()))
    elif args.command == "chat":
        client.connect()
        result = client.call("message", prompt=args.prompt,
                             request_id=args.request_id or uuid.uuid4().hex)
        print(json.dumps(result), flush=True)
        try:
            final = client.watch(result["run_id"])
        except KeyboardInterrupt:
            client.call("cancel")
            final = client.watch(result["run_id"])
        if final["status"] != "ok":
            raise SystemExit(1)
    elif args.command == "events":
        client.watch(args.run_id, after=args.after)
    else:
        print(json.dumps(client.call(args.command)))


if __name__ == "__main__":
    main()
