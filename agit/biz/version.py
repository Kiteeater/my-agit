"""Push and read harness versions. The id hashes surfaces that are present."""

import hashlib

from agit.biz.project import authorized_project
from agit.data.store import Store, sorted_versions, utc_now, version_dict
from agit.domain import Harness, Project, Version, as_harness
from agit.judge import AgitError


def push_version(
    store: Store,
    project_id: str,
    api_key: str,
    skill: str,
    prompt: str,
    message: str,
    *,
    tool: str | None = None,
) -> dict[str, object]:
    """Store a harness. tool is optional and is not part of the CLI or HTTP body."""
    skill_text = require_text(skill, "skill")
    prompt_text = require_text(prompt, "prompt")
    message_text = require_text(message, "message")
    if tool is None:
        tool_text = None
    else:
        tool_text = require_text(tool, "tool")
    harness = Harness(skill=skill_text, prompt=prompt_text, tool=tool_text)
    version = Version(
        version_id=version_id_for(harness),
        harness=harness,
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


def version_id_for(subject: Harness | Version) -> str:
    """Hash the harness surfaces that are present.

    Skill and prompt alone use SHA-256 of the skill bytes, a NUL, and the
    prompt bytes. That digest matches versions stored before optional
    surfaces existed. A present tool is tagged into the digest so it cannot
    collide with that older encoding.
    """
    harness = as_harness(subject)
    if harness.tool is None:
        payload = harness.skill.encode("utf-8") + b"\0" + harness.prompt.encode("utf-8")
    else:
        payload = (
            b"skill\0"
            + harness.skill.encode("utf-8")
            + b"\0prompt\0"
            + harness.prompt.encode("utf-8")
            + b"\0tool\0"
            + harness.tool.encode("utf-8")
        )
    return hashlib.sha256(payload).hexdigest()


def require_version(project: Project, version_id: str) -> Version:
    version = project.versions.get(version_id)
    if version is None:
        raise AgitError("version not found", 404)
    return version


def require_text(value: str, field_name: str) -> str:
    if value.strip() == "":
        raise AgitError(f"{field_name} must not be empty", 400)
    return value
