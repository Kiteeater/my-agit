"""HTTP API over the project store."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

from agit.biz.project import create_project, get_project
from agit.biz.release import bootstrap_black, compare_red_black, list_releases, mark_red, run_gate
from agit.biz.version import get_version, list_versions, push_version
from agit.data.store import Store
from agit.judge import AgitError, make_judge

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_BODY_BYTES = 1_000_000


class AgitHTTPServer(HTTPServer):
    allow_reuse_address = True


def build_server(host: str, port: int, store_path: Path) -> AgitHTTPServer:
    return AgitHTTPServer((host, port), handler_for(Store(store_path)))


def serve(host: str, port: int, store_path: Path) -> None:
    server = build_server(host, port, store_path)
    print(f"listening on http://{host}:{port}", flush=True)
    server.serve_forever()


def handler_for(store: Store) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.respond("GET")

        def do_POST(self) -> None:
            self.respond("POST")

        def respond(self, method: str) -> None:
            try:
                status, payload = dispatch(
                    method,
                    self.path,
                    read_json_object(self) if method == "POST" else None,
                    bearer_token(self.headers.get("Authorization")),
                    store,
                )
            except AgitError as agit_error:
                status, payload = agit_error.status_code, {"error": agit_error.message}
            write_json(self, status, payload)

    return Handler


def dispatch(
    method: str,
    path: str,
    body: dict[str, object] | None,
    api_key: str | None,
    store: Store,
) -> tuple[int, dict[str, object]]:
    parts = [part for part in urlparse(path).path.split("/") if part != ""]
    if parts == ["v1", "projects"] and method == "POST":
        payload = require_body(body)
        created = create_project(store, require_string(payload, "name"), require_string(payload, "git_remote_url"))
        return 201, created
    if len(parts) < 3 or parts[0] != "v1" or parts[1] != "projects":
        raise AgitError("not found", 404)
    project_id = parts[2]
    key = require_api_key(api_key)
    tail = parts[3:]
    if tail == [] and method == "GET":
        return 200, get_project(store, project_id, key)
    if tail == ["versions"] and method == "GET":
        return 200, list_versions(store, project_id, key)
    if tail == ["versions"] and method == "POST":
        payload = require_body(body)
        return 200, push_version(
            store,
            project_id,
            key,
            require_string(payload, "skill"),
            require_string(payload, "prompt"),
            require_string(payload, "message"),
        )
    if len(tail) == 2 and tail[0] == "versions" and method == "GET":
        return 200, get_version(store, project_id, key, tail[1])
    if tail == ["black"] and method == "POST":
        payload = require_body(body)
        return 200, bootstrap_black(store, project_id, key, require_string(payload, "version_id"))
    if tail == ["red"] and method == "POST":
        payload = require_body(body)
        return 200, mark_red(store, project_id, key, require_string(payload, "version_id"))
    if tail == ["compare"] and method == "POST":
        payload = require_body(body)
        return 200, compare_red_black(store, project_id, key, make_judge(judge_mode(payload)))
    if tail == ["gate"] and method == "POST":
        payload = require_body(body)
        return 200, run_gate(store, project_id, key, margin_value(payload), make_judge(judge_mode(payload)))
    if tail == ["releases"] and method == "GET":
        return 200, list_releases(store, project_id, key)
    if tail in (["versions"], ["black"], ["red"], ["compare"], ["gate"], ["releases"]) or tail == []:
        raise AgitError("method not allowed", 405)
    raise AgitError("not found", 404)


def require_body(body: dict[str, object] | None) -> dict[str, object]:
    if body is None:
        raise AgitError("request body must be a JSON object", 400)
    return body


def require_api_key(api_key: str | None) -> str:
    if api_key is None:
        raise AgitError("missing Authorization bearer token", 401)
    return api_key


def require_string(body: dict[str, object], field_name: str) -> str:
    value = body.get(field_name)
    if not isinstance(value, str):
        raise AgitError(f"{field_name} must be a string", 400)
    return value


def margin_value(body: dict[str, object]) -> float:
    if "margin" not in body:
        return 0.0
    value = body["margin"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AgitError("margin must be a number", 400)
    return float(value)


def judge_mode(body: dict[str, object]) -> str:
    if "judge" not in body:
        return "auto"
    value = body["judge"]
    if not isinstance(value, str) or value not in ("auto", "stub"):
        raise AgitError("judge must be auto or stub", 400)
    return value


def bearer_token(header_value: str | None) -> str | None:
    if header_value is None:
        return None
    prefix = "Bearer "
    if not header_value.startswith(prefix) or header_value[len(prefix):].strip() == "":
        raise AgitError("Authorization must be a Bearer token", 401)
    return header_value[len(prefix):].strip()


def read_json_object(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    length_header = handler.headers.get("Content-Length")
    if length_header is None:
        raise AgitError("Content-Length is required", 400)
    try:
        length = int(length_header)
    except ValueError as value_error:
        raise AgitError("Content-Length must be an integer", 400) from value_error
    if length < 0 or length > MAX_BODY_BYTES:
        raise AgitError("request body is too large", 400)
    if length == 0:
        return {}
    try:
        text = handler.rfile.read(length).decode("utf-8")
    except UnicodeDecodeError as decode_error:
        raise AgitError("request body must be UTF-8", 400) from decode_error
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as decode_error:
        raise AgitError("request body must be JSON", 400) from decode_error
    if not isinstance(parsed, dict):
        raise AgitError("request body must be a JSON object", 400)
    return parsed


def write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, object]) -> None:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)
