"""Skill and prompt versions, and the red/black release gate."""

import hashlib
import hmac
import math
import secrets
from decimal import Decimal

from agit.bench import load_bench
from agit.judge import AgitError, HarnessScore, OpenAIJudge, StubJudge
from agit.store import (
    Project,
    ReleaseAction,
    ReleaseEvent,
    Store,
    Version,
    release_dict,
    sorted_versions,
    utc_now,
    version_dict,
)

MAX_PROJECT_NAME_LENGTH = 80
Judge = StubJudge | OpenAIJudge


def create_project(store: Store, name: str, git_remote_url: str) -> dict[str, object]:
    project_name = normalize_name(name)
    remote = normalize_git_remote_url(git_remote_url)
    api_key = "agk_" + secrets.token_hex(24)
    project = Project(
        project_id="proj_" + secrets.token_hex(8),
        name=project_name,
        git_remote_url=remote,
        api_key_sha256=hashlib.sha256(api_key.encode("utf-8")).hexdigest(),
        created_at=utc_now(),
    )

    def mutate(projects: dict[str, Project]) -> Project:
        projects[project.project_id] = project
        return project

    store.update(mutate)
    summary = project_summary(project)
    summary["api_key"] = api_key
    return summary


def push_version(
    store: Store,
    project_id: str,
    api_key: str,
    skill: str,
    prompt: str,
    message: str,
) -> dict[str, object]:
    skill_text = require_text(skill, "skill")
    prompt_text = require_text(prompt, "prompt")
    message_text = require_text(message, "message")
    version = Version(
        version_id=version_id_for(skill_text, prompt_text),
        skill=skill_text,
        prompt=prompt_text,
        message=message_text,
        created_at=utc_now(),
    )

    def mutate(projects: dict[str, Project]) -> tuple[Version, bool]:
        project = authorized_project(projects, project_id, api_key)
        existing = project.versions.get(version.version_id)
        if existing is not None:
            return existing, False
        project.versions[version.version_id] = version
        return version, True

    stored, created = store.update(mutate)
    payload = version_dict(stored)
    payload["created"] = created
    return payload


def list_versions(store: Store, project_id: str, api_key: str) -> dict[str, object]:
    project = authorized_project(store.read(), project_id, api_key)
    return {"versions": [version_dict(version) for version in sorted_versions(project)]}


def get_version(store: Store, project_id: str, api_key: str, version_id: str) -> dict[str, object]:
    project = authorized_project(store.read(), project_id, api_key)
    return version_dict(require_version(project, version_id))


def get_project(store: Store, project_id: str, api_key: str) -> dict[str, object]:
    return project_summary(authorized_project(store.read(), project_id, api_key))


def list_releases(store: Store, project_id: str, api_key: str) -> dict[str, object]:
    project = authorized_project(store.read(), project_id, api_key)
    return {"releases": [release_dict(event) for event in project.releases]}


def bootstrap_black(store: Store, project_id: str, api_key: str, version_id: str) -> dict[str, object]:
    """Mark the first live version. Later black changes go through the gate."""

    def mutate(projects: dict[str, Project]) -> Project:
        project = authorized_project(projects, project_id, api_key)
        if project.black_version_id is not None:
            raise AgitError("black is already set; promote a red candidate with the gate", 409)
        require_version(project, version_id)
        project.black_version_id = version_id
        project.releases.append(
            ReleaseEvent(
                at=utc_now(),
                action="bootstrap_black",
                version_id=version_id,
                black_version_id=version_id,
                red_version_id=project.red_version_id,
                detail="initial live version",
            )
        )
        return project

    return project_summary(store.update(mutate))


def mark_red(store: Store, project_id: str, api_key: str, version_id: str) -> dict[str, object]:
    def mutate(projects: dict[str, Project]) -> Project:
        project = authorized_project(projects, project_id, api_key)
        if project.black_version_id is None:
            raise AgitError("set a black version before marking a red candidate", 409)
        if version_id == project.black_version_id:
            raise AgitError("red candidate must be a different version from black", 409)
        require_version(project, version_id)
        project.red_version_id = version_id
        project.releases.append(
            ReleaseEvent(
                at=utc_now(),
                action="set_red",
                version_id=version_id,
                black_version_id=project.black_version_id,
                red_version_id=version_id,
                detail="candidate marked",
            )
        )
        return project

    return project_summary(store.update(mutate))


def compare_red_black(store: Store, project_id: str, api_key: str, judge: Judge) -> dict[str, object]:
    project = authorized_project(store.read(), project_id, api_key)
    black, red = live_pair(project)
    bench = load_bench()
    black_score = judge.score_harness(black.skill, black.prompt, bench)
    red_score = judge.score_harness(red.skill, red.prompt, bench)
    return compare_dict(black.version_id, red.version_id, black_score, red_score)


def run_gate(
    store: Store,
    project_id: str,
    api_key: str,
    margin: float,
    judge: Judge,
) -> dict[str, object]:
    """Promote red only when it strictly leads black by at least margin."""
    if math.isnan(margin) or math.isinf(margin) or margin < 0:
        raise AgitError("margin must be a finite number >= 0", 400)
    report = compare_red_black(store, project_id, api_key, judge)
    red_points = int(report["red_points"])
    black_points = int(report["black_points"])
    max_points = int(report["max_points"])
    promoted = gate_passes(red_points, black_points, max_points, margin)
    if promoted:
        detail = (
            f"promoted by {judge.name}: red {red_points}/{max_points} "
            f"vs black {black_points}/{max_points} (margin {margin})"
        )
    else:
        detail = (
            f"kept black: red {red_points}/{max_points} "
            f"vs black {black_points}/{max_points} (margin {margin})"
        )

    def mutate(projects: dict[str, Project]) -> Project:
        project = authorized_project(projects, project_id, api_key)
        if project.black_version_id != report["black_version_id"] or project.red_version_id != report["red_version_id"]:
            raise AgitError("red or black changed during scoring", 409)
        action: ReleaseAction = "promote" if promoted else "reject"
        if promoted:
            project.black_version_id = project.red_version_id
            project.red_version_id = None
        project.releases.append(
            ReleaseEvent(
                at=utc_now(),
                action=action,
                version_id=str(report["red_version_id"]),
                black_version_id=project.black_version_id,
                red_version_id=project.red_version_id,
                detail=detail,
                judge_name=judge.name,
                model=judge.model,
                red_points=red_points,
                black_points=black_points,
                max_points=max_points,
                margin=margin,
            )
        )
        return project

    project = store.update(mutate)
    return {
        "promoted": promoted,
        "margin": margin,
        "detail": detail,
        "judge_name": judge.name,
        "model": judge.model,
        "black_version_id": project.black_version_id,
        "red_version_id": project.red_version_id,
        "red_points": red_points,
        "black_points": black_points,
        "max_points": max_points,
        "compare": report,
    }


def gate_passes(red_points: int, black_points: int, max_points: int, margin: float) -> bool:
    assert max_points > 0
    assert margin >= 0
    lead = Decimal(red_points - black_points)
    required = Decimal(str(margin)) * Decimal(max_points)
    return lead > 0 and lead >= required


def version_id_for(skill: str, prompt: str) -> str:
    return hashlib.sha256(skill.encode("utf-8") + b"\0" + prompt.encode("utf-8")).hexdigest()


def normalize_name(name: str) -> str:
    project_name = name.strip()
    if project_name == "" or "\n" in project_name or len(project_name) > MAX_PROJECT_NAME_LENGTH:
        raise AgitError("name must be 1-80 characters on one line", 400)
    return project_name


def normalize_git_remote_url(raw: str) -> str:
    git_remote_url = raw.strip()
    host_and_path = git_remote_url.split("@", 1)[1] if git_remote_url.startswith("git@") else ""
    if git_remote_url.startswith("git@") and ":" in host_and_path:
        return git_remote_url
    if git_remote_url.startswith(("https://", "http://", "ssh://")) and " " not in git_remote_url:
        rest = git_remote_url.split("://", 1)[1]
        host, separator, path = rest.partition("/")
        if host != "" and separator == "/" and path != "":
            return git_remote_url
    raise AgitError("git_remote_url must be git@host:path or an http(s)/ssh URL", 400)


def require_text(value: str, field_name: str) -> str:
    if value.strip() == "":
        raise AgitError(f"{field_name} must not be empty", 400)
    return value


def authorized_project(projects: dict[str, Project], project_id: str, api_key: str) -> Project:
    project = projects.get(project_id)
    if project is None:
        raise AgitError("project not found", 404)
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(digest, project.api_key_sha256):
        raise AgitError("invalid api key", 401)
    return project


def require_version(project: Project, version_id: str) -> Version:
    version = project.versions.get(version_id)
    if version is None:
        raise AgitError("version not found", 404)
    return version


def live_pair(project: Project) -> tuple[Version, Version]:
    if project.black_version_id is None or project.red_version_id is None:
        raise AgitError("black and red must both be set before compare", 409)
    return require_version(project, project.black_version_id), require_version(project, project.red_version_id)


def compare_dict(
    black_version_id: str,
    red_version_id: str,
    black_score: HarnessScore,
    red_score: HarnessScore,
) -> dict[str, object]:
    assert black_score.max_points == red_score.max_points
    return {
        "judge_name": red_score.judge_name,
        "model": red_score.model,
        "black_version_id": black_version_id,
        "red_version_id": red_version_id,
        "black_points": black_score.points,
        "red_points": red_score.points,
        "max_points": red_score.max_points,
        "delta_points": red_score.points - black_score.points,
        "black": black_score.to_dict(black_version_id),
        "red": red_score.to_dict(red_version_id),
    }


def project_summary(project: Project) -> dict[str, object]:
    return {
        "project_id": project.project_id,
        "name": project.name,
        "git_remote_url": project.git_remote_url,
        "created_at": project.created_at,
        "black_version_id": project.black_version_id,
        "red_version_id": project.red_version_id,
        "version_ids": [version.version_id for version in sorted_versions(project)],
    }


