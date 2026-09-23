"""JSON file store for projects, versions, and release history."""

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from agit.domain import Harness, Project, ReleaseAction, ReleaseEvent, Version

DEFAULT_STORE_PATH = Path(".agit") / "store.json"
T = TypeVar("T")


class Store:
    """One JSON file. Writes replace the file only after a mutation returns."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, Project]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        projects = payload["projects"]
        return {project_id: project_from_dict(record) for project_id, record in projects.items()}

    def write(self, projects: dict[str, Project]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"projects": {project_id: project_to_dict(project) for project_id, project in projects.items()}}
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def update(self, mutate: Callable[[dict[str, Project]], T]) -> T:
        projects = self.read()
        value = mutate(projects)
        self.write(projects)
        return value


def sorted_versions(project: Project) -> list[Version]:
    return sorted(project.versions.values(), key=lambda version: (version.created_at, version.version_id))


def version_dict(version: Version) -> dict[str, object]:
    """Flat skill and prompt keys match store files written before Harness existed.

    Optional surfaces are omitted when absent so a skill/prompt version keeps
    the old object shape.
    """
    payload = {
        "version_id": version.version_id,
        "skill": version.harness.skill,
        "prompt": version.harness.prompt,
        "message": version.message,
        "created_at": version.created_at,
    }
    if version.harness.tool is None:
        return payload
    else:
        payload["tool"] = version.harness.tool
        return payload


def release_dict(event: ReleaseEvent) -> dict[str, object]:
    return {
        "at": event.at,
        "action": event.action,
        "version_id": event.version_id,
        "black_version_id": event.black_version_id,
        "red_version_id": event.red_version_id,
        "detail": event.detail,
        "judge_name": event.judge_name,
        "model": event.model,
        "red_points": event.red_points,
        "black_points": event.black_points,
        "max_points": event.max_points,
        "margin": event.margin,
    }


def project_to_dict(project: Project) -> dict[str, object]:
    return {
        "project_id": project.project_id,
        "name": project.name,
        "git_remote_url": project.git_remote_url,
        "api_key_sha256": project.api_key_sha256,
        "created_at": project.created_at,
        "black_version_id": project.black_version_id,
        "red_version_id": project.red_version_id,
        "versions": {version.version_id: version_dict(version) for version in sorted_versions(project)},
        "releases": [release_dict(event) for event in project.releases],
    }


def project_from_dict(record: object) -> Project:
    assert isinstance(record, dict)
    versions_raw = record["versions"]
    releases_raw = record["releases"]
    assert isinstance(versions_raw, dict) and isinstance(releases_raw, list)
    versions: dict[str, Version] = {}
    for version_id, version_raw in versions_raw.items():
        assert isinstance(version_raw, dict)
        if "tool" in version_raw:
            tool = expect_optional_str(version_raw["tool"], "tool")
        else:
            tool = None
        version = Version(
            version_id=expect_str(version_raw["version_id"], "version_id"),
            harness=Harness(
                skill=expect_str(version_raw["skill"], "skill"),
                prompt=expect_str(version_raw["prompt"], "prompt"),
                tool=tool,
            ),
            message=expect_str(version_raw["message"], "message"),
            created_at=expect_str(version_raw["created_at"], "created_at"),
        )
        assert version.version_id == version_id
        versions[version_id] = version
    return Project(
        project_id=expect_str(record["project_id"], "project_id"),
        name=expect_str(record["name"], "name"),
        git_remote_url=expect_str(record["git_remote_url"], "git_remote_url"),
        api_key_sha256=expect_str(record["api_key_sha256"], "api_key_sha256"),
        created_at=expect_str(record["created_at"], "created_at"),
        versions=versions,
        black_version_id=expect_optional_str(record["black_version_id"], "black_version_id"),
        red_version_id=expect_optional_str(record["red_version_id"], "red_version_id"),
        releases=[release_from_dict(item) for item in releases_raw],
    )


def release_from_dict(record: object) -> ReleaseEvent:
    assert isinstance(record, dict)
    return ReleaseEvent(
        at=expect_str(record["at"], "at"),
        action=expect_action(record["action"]),
        version_id=expect_str(record["version_id"], "version_id"),
        black_version_id=expect_optional_str(record["black_version_id"], "black_version_id"),
        red_version_id=expect_optional_str(record["red_version_id"], "red_version_id"),
        detail=expect_str(record["detail"], "detail"),
        judge_name=expect_optional_str(record["judge_name"], "judge_name"),
        model=expect_optional_str(record["model"], "model"),
        red_points=expect_optional_int(record["red_points"], "red_points"),
        black_points=expect_optional_int(record["black_points"], "black_points"),
        max_points=expect_optional_int(record["max_points"], "max_points"),
        margin=expect_optional_float(record["margin"], "margin"),
    )


def expect_action(value: object) -> ReleaseAction:
    if value == "bootstrap_black":
        return "bootstrap_black"
    if value == "set_red":
        return "set_red"
    if value == "promote":
        return "promote"
    if value == "reject":
        return "reject"
    raise ValueError(f"unknown release action: {value}")


def expect_str(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def expect_optional_str(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return expect_str(value, field_name)


def expect_optional_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer or null")
    return value


def expect_optional_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number or null")
    return float(value)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
