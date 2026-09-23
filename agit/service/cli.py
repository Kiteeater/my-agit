"""Command line for the project store, versions, and the release gate."""

import argparse
import json
import os
import sys
from pathlib import Path

from agit.bench import FIXTURE_BENCH
from agit.biz.project import create_project
from agit.biz.release import bootstrap_black, compare_red_black, list_releases, mark_red, run_gate
from agit.biz.version import get_version, list_versions, push_version
from agit.data.store import DEFAULT_STORE_PATH, Store
from agit.judge import JUDGE_MODES, AgitError, make_judge
from agit.service.api import DEFAULT_HOST, DEFAULT_PORT, serve
from agit.service.demo import run_demo


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        print_json(execute(args))
    except AgitError as agit_error:
        print(agit_error.message, file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agit", description="Version control and a red/black gate for agent harnesses")
    parser.add_argument("--store", default=os.environ.get("AGIT_STORE", str(DEFAULT_STORE_PATH)))
    commands = parser.add_subparsers(dest="command", required=True)

    project = commands.add_parser("project")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    create = project_commands.add_parser("create")
    create.add_argument("--name", required=True)
    create.add_argument("--git-url", required=True)

    auth = argparse.ArgumentParser(add_help=False)
    auth.add_argument("--project", default=os.environ.get("AGIT_PROJECT"))
    auth.add_argument("--key", default=os.environ.get("AGIT_API_KEY"))

    version = commands.add_parser("version")
    version_commands = version.add_subparsers(dest="version_command", required=True)
    push = version_commands.add_parser("push", parents=[auth])
    push.add_argument("--skill-file", required=True)
    push.add_argument("--prompt-file", required=True)
    push.add_argument("--tool-file")
    push.add_argument("--message", required=True)
    version_commands.add_parser("list", parents=[auth])
    show = version_commands.add_parser("show", parents=[auth])
    show.add_argument("--version", required=True)

    release = commands.add_parser("release")
    release_commands = release.add_subparsers(dest="release_command", required=True)
    black = release_commands.add_parser("black", parents=[auth])
    black.add_argument("--version", required=True)
    red = release_commands.add_parser("red", parents=[auth])
    red.add_argument("--version", required=True)
    release_commands.add_parser("list", parents=[auth])

    compare = commands.add_parser("compare", parents=[auth])
    compare.add_argument("--judge", choices=JUDGE_MODES, default="auto")
    compare.add_argument("--bench", default=FIXTURE_BENCH)
    gate = commands.add_parser("gate", parents=[auth])
    gate.add_argument("--margin", type=float, default=0.0)
    gate.add_argument("--judge", choices=JUDGE_MODES, default="auto")
    gate.add_argument("--bench", default=FIXTURE_BENCH)

    server = commands.add_parser("serve")
    server.add_argument("--host", default=DEFAULT_HOST)
    server.add_argument("--port", type=int, default=DEFAULT_PORT)
    commands.add_parser("demo")
    return parser


def execute(args: argparse.Namespace) -> dict[str, object] | None:
    if args.command == "serve":
        serve(args.host, args.port, Path(args.store))
        return None
    if args.command == "demo":
        return run_demo()
    store = Store(Path(args.store))
    if args.command == "project" and args.project_command == "create":
        return create_project(store, args.name, args.git_url)
    project_id, api_key = require_auth(args)
    if args.command == "version" and args.version_command == "push":
        if args.tool_file is None:
            tool = None
        else:
            tool = read_text(args.tool_file)
        return push_version(
            store,
            project_id,
            api_key,
            read_text(args.skill_file),
            read_text(args.prompt_file),
            args.message,
            tool=tool,
        )
    if args.command == "version" and args.version_command == "list":
        return list_versions(store, project_id, api_key)
    if args.command == "version" and args.version_command == "show":
        return get_version(store, project_id, api_key, args.version)
    if args.command == "release" and args.release_command == "black":
        return bootstrap_black(store, project_id, api_key, args.version)
    if args.command == "release" and args.release_command == "red":
        return mark_red(store, project_id, api_key, args.version)
    if args.command == "release" and args.release_command == "list":
        return list_releases(store, project_id, api_key)
    if args.command == "compare":
        return compare_red_black(store, project_id, api_key, make_judge(args.judge), args.bench)
    if args.command == "gate":
        return run_gate(store, project_id, api_key, args.margin, make_judge(args.judge), args.bench)
    raise AgitError("unknown command", 400)


def read_text(path: str) -> str:
    file_path = Path(path)
    if not file_path.is_file():
        raise AgitError(f"file not found: {path}", 400)
    return file_path.read_text(encoding="utf-8")


def require_auth(args: argparse.Namespace) -> tuple[str, str]:
    project_id = args.project
    api_key = args.key
    if not isinstance(project_id, str) or project_id == "":
        raise AgitError("pass --project or set AGIT_PROJECT", 400)
    if not isinstance(api_key, str) or api_key == "":
        raise AgitError("pass --key or set AGIT_API_KEY", 400)
    return project_id, api_key


def print_json(payload: dict[str, object] | None) -> None:
    if payload is None:
        return
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
