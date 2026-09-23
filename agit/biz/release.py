"""Red/black release: bootstrap, mark, compare, and the promotion gate."""

import math
from decimal import Decimal

from agit.bench import load_bench
from agit.biz.project import authorized_project, project_summary
from agit.biz.version import require_version
from agit.data.store import Store, release_dict, utc_now
from agit.domain import Project, ReleaseAction, ReleaseEvent, Version
from agit.judge import AgitError, HarnessScore, OpenAIJudge, StubJudge

Judge = StubJudge | OpenAIJudge


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
    black_score = judge.score_harness(black, bench)
    red_score = judge.score_harness(red, bench)
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
