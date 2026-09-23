"""One offline pass: weaker red stays off black, stronger red becomes black."""

from pathlib import Path
from tempfile import TemporaryDirectory

from agit.biz.project import create_project
from agit.biz.release import bootstrap_black, list_releases, mark_red, run_gate
from agit.biz.version import push_version
from agit.data.store import Store
from agit.judge import StubJudge

DEMO_GIT_REMOTE_URL = "git@github.com:acme/support-agent.git"
DEMO_NAME = "support-agent"


def run_demo() -> dict[str, object]:
    with TemporaryDirectory() as directory:
        return run_in_store(Path(directory) / "store.json")


def run_in_store(store_path: Path) -> dict[str, object]:
    store = Store(store_path)
    judge = StubJudge()
    created = create_project(store, DEMO_NAME, DEMO_GIT_REMOTE_URL)
    project_id = str(created["project_id"])
    api_key = str(created["api_key"])
    baseline = push_version(
        store,
        project_id,
        api_key,
        example_text("baseline_skill.md"),
        example_text("baseline_prompt.md"),
        "baseline harness",
    )
    baseline_id = str(baseline["version_id"])
    bootstrap_black(store, project_id, api_key, baseline_id)
    weaker = push_version(
        store,
        project_id,
        api_key,
        example_text("weaker_skill.md"),
        example_text("weaker_prompt.md"),
        "weaker candidate",
    )
    weaker_id = str(weaker["version_id"])
    mark_red(store, project_id, api_key, weaker_id)
    weaker_gate = run_gate(store, project_id, api_key, 0.0, judge)
    stronger = push_version(
        store,
        project_id,
        api_key,
        example_text("stronger_skill.md"),
        example_text("stronger_prompt.md"),
        "stronger candidate",
    )
    stronger_id = str(stronger["version_id"])
    mark_red(store, project_id, api_key, stronger_id)
    stronger_gate = run_gate(store, project_id, api_key, 0.0, judge)
    release_list = list_releases(store, project_id, api_key)["releases"]
    assert isinstance(release_list, list)
    actions: list[str] = []
    for event in release_list:
        assert isinstance(event, dict)
        actions.append(str(event["action"]))
    assert weaker_gate["promoted"] is False
    assert weaker_gate["black_version_id"] == baseline_id
    assert int(weaker_gate["red_points"]) == 0
    assert int(weaker_gate["black_points"]) == 12
    assert stronger_gate["promoted"] is True
    assert stronger_gate["black_version_id"] == stronger_id
    assert stronger_gate["red_version_id"] is None
    assert int(stronger_gate["red_points"]) == 18
    assert int(stronger_gate["black_points"]) == 12
    assert actions == ["bootstrap_black", "set_red", "reject", "set_red", "promote"]
    return {
        "project_id": project_id,
        "git_remote_url": DEMO_GIT_REMOTE_URL,
        "judge_name": judge.name,
        "baseline_version_id": baseline_id,
        "baseline_points": weaker_gate["black_points"],
        "max_points": weaker_gate["max_points"],
        "weaker": {
            "version_id": weaker_id,
            "promoted": weaker_gate["promoted"],
            "red_points": weaker_gate["red_points"],
            "black_points": weaker_gate["black_points"],
            "black_version_id": weaker_gate["black_version_id"],
        },
        "stronger": {
            "version_id": stronger_id,
            "promoted": stronger_gate["promoted"],
            "red_points": stronger_gate["red_points"],
            "black_points": stronger_gate["black_points"],
            "black_version_id": stronger_gate["black_version_id"],
        },
        "release_actions": actions,
        "final_black_version_id": stronger_gate["black_version_id"],
        "final_red_version_id": stronger_gate["red_version_id"],
    }


def example_text(name: str) -> str:
    path = Path(__file__).resolve().parents[2] / "examples" / name
    return path.read_text(encoding="utf-8")
