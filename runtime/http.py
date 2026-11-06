import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen


def request(base, path, data=None, token="", timeout=65):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    body = None if data is None else json.dumps(data).encode()
    with urlopen(Request(base + path, data=body, headers=headers), timeout=timeout) as r:
        return json.load(r)


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def server(address, dispatch, token):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.handle_api()

        def do_POST(self):
            self.handle_api()

        def handle_api(self):
            self.connection.settimeout(70)
            try:
                if self.path != "/health" and self.headers.get("Authorization") != f"Bearer {token}":
                    raise APIError(401, "unauthorized")
                length = int(self.headers.get("Content-Length", 0))
                if not 0 <= length <= 1_000_000:
                    raise APIError(413, "body too large")
                data = json.loads(self.rfile.read(length)) if length else {}
                if not isinstance(data, dict):
                    raise ValueError("object required")
                result = dispatch(self.command, self.path, data)
                status = 200
            except APIError as e:
                status, result = e.status, {"error": e.message}
            except (ValueError, KeyError, TypeError):
                status, result = 400, {"error": "invalid request"}
            except Exception:
                status, result = 500, {"error": "internal error"}
            body = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(address, Handler)
