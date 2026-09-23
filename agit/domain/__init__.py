"""Pure models. No I/O and no release rules."""

from dataclasses import dataclass, field
from typing import Literal

ReleaseAction = Literal["bootstrap_black", "set_red", "promote", "reject"]


@dataclass(kw_only=True, frozen=True)
class Harness:
    """Agent harness.

    Skill and prompt are the live surfaces. tool is a slot for a later
    surface: None means that surface is not part of this harness.
    """

    skill: str
    prompt: str
    tool: str | None = None


@dataclass(kw_only=True)
class Version:
    version_id: str
    harness: Harness
    message: str
    created_at: str


@dataclass(kw_only=True)
class ReleaseEvent:
    at: str
    action: ReleaseAction
    version_id: str
    black_version_id: str | None
    red_version_id: str | None
    detail: str
    judge_name: str | None = None
    model: str | None = None
    red_points: int | None = None
    black_points: int | None = None
    max_points: int | None = None
    margin: float | None = None


@dataclass(kw_only=True)
class Project:
    project_id: str
    name: str
    git_remote_url: str
    api_key_sha256: str
    created_at: str
    versions: dict[str, Version] = field(default_factory=dict)
    black_version_id: str | None = None
    red_version_id: str | None = None
    releases: list[ReleaseEvent] = field(default_factory=list)


def as_harness(subject: Harness | Version) -> Harness:
    if isinstance(subject, Harness):
        return subject
    else:
        return subject.harness


__all__ = [
    "Harness",
    "Project",
    "ReleaseAction",
    "ReleaseEvent",
    "Version",
    "as_harness",
]
