"""Load a bench and its rubric for harness scoring."""

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

FULL_POINTS = 2
FIXTURE_BENCH = "fixture"
HOT_BENCH = "hot"


@dataclass(kw_only=True)
class RubricCriterion:
    criterion_id: str
    max_points: int
    text: str


@dataclass(kw_only=True)
class BenchCase:
    case_id: str
    task: str
    checks: dict[str, tuple[str, ...]]


@dataclass(kw_only=True)
class Bench:
    criteria: tuple[RubricCriterion, ...]
    cases: tuple[BenchCase, ...]

    def max_points(self) -> int:
        return len(self.cases) * sum(criterion.max_points for criterion in self.criteria)


def load_bench(spec: str = FIXTURE_BENCH) -> Bench:
    """Load the fixture bench, the hot sample, or a JSON file of the same shape.

    The names fixture and hot are built in. Any other spec must name an existing file.
    """
    if spec == FIXTURE_BENCH:
        text = files("agit").joinpath("fixtures.json").read_text(encoding="utf-8")
    elif spec == HOT_BENCH:
        text = files("agit").joinpath("hot_fixtures.json").read_text(encoding="utf-8")
    else:
        path = Path(spec)
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as decode_error:
                raise bench_error(f"bench file is not UTF-8: {spec}") from decode_error
        else:
            raise bench_error(f"unknown bench {spec}; choose fixture, hot, or a file path")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as decode_error:
        raise bench_error("bench file is not JSON") from decode_error
    if not isinstance(raw, dict):
        raise bench_error("bench JSON must be an object")
    rubric = raw.get("rubric")
    case_items = raw.get("cases")
    if not isinstance(rubric, list) or len(rubric) == 0:
        raise bench_error("bench rubric must be a non-empty list")
    if not isinstance(case_items, list) or len(case_items) == 0:
        raise bench_error("bench cases must be a non-empty list")
    criteria: list[RubricCriterion] = []
    seen_criteria: set[str] = set()
    for item in rubric:
        if not isinstance(item, dict):
            raise bench_error("bench rubric entries must be objects")
        criterion_id = item.get("id")
        criterion_text = item.get("text")
        max_points = item.get("max_points")
        if not isinstance(criterion_id, str) or criterion_id == "":
            raise bench_error("bench criterion id must be a non-empty string")
        if criterion_id in seen_criteria:
            raise bench_error(f"duplicate bench criterion {criterion_id}")
        seen_criteria.add(criterion_id)
        if not isinstance(criterion_text, str) or criterion_text == "":
            raise bench_error(f"bench criterion {criterion_id} needs text")
        if type(max_points) is not int or max_points != FULL_POINTS:
            raise bench_error(f"bench criterion {criterion_id} max_points must be {FULL_POINTS}")
        criteria.append(
            RubricCriterion(
                criterion_id=criterion_id,
                max_points=max_points,
                text=criterion_text,
            )
        )
    cases: list[BenchCase] = []
    seen_cases: set[str] = set()
    for item in case_items:
        if not isinstance(item, dict):
            raise bench_error("bench cases must be objects")
        case_id = item.get("id")
        task = item.get("task")
        checks_raw = item.get("checks")
        if not isinstance(case_id, str) or case_id == "":
            raise bench_error("bench case id must be a non-empty string")
        if case_id in seen_cases:
            raise bench_error(f"duplicate bench case {case_id}")
        seen_cases.add(case_id)
        if not isinstance(task, str) or task == "":
            raise bench_error(f"bench case {case_id} needs a task")
        if not isinstance(checks_raw, dict):
            raise bench_error(f"bench case {case_id} checks must be an object")
        checks: dict[str, tuple[str, ...]] = {}
        for criterion in criteria:
            needles = checks_raw.get(criterion.criterion_id)
            if not isinstance(needles, list) or len(needles) == 0:
                raise bench_error(
                    f"bench case {case_id} missing checks for {criterion.criterion_id}"
                )
            if any(not isinstance(needle, str) or needle == "" for needle in needles):
                raise bench_error(
                    f"bench case {case_id} checks for {criterion.criterion_id} must be non-empty strings"
                )
            checks[criterion.criterion_id] = tuple(needles)
        cases.append(BenchCase(case_id=case_id, task=task, checks=checks))
    return Bench(criteria=tuple(criteria), cases=tuple(cases))


def bench_error(message: str) -> Exception:
    # note (luominyu): imported here because judge imports this module while it is still loading
    from agit.judge import AgitError

    return AgitError(message, 400)
