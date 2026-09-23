"""Create a project, read it, and check its API key."""

import hashlib
import hmac
import secrets

from agit.data.store import Store, sorted_versions, utc_now
from agit.domain import Project
from agit.judge import AgitError

MAX_PROJECT_NAME_LENGTH = 80


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


def get_project(store: Store, project_id: str, api_key: str) -> dict[str, object]:
    return project_summary(authorized_project(store.read(), project_id, api_key))


def authorized_project(projects: dict[str, Project], project_id: str, api_key: str) -> Project:
    project = projects.get(project_id)
    if project is None:
        raise AgitError("project not found", 404)
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(digest, project.api_key_sha256):
        raise AgitError("invalid api key", 401)
    return project


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
