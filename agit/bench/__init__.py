"""Fixture benchmark and rubric for harness scoring."""

from dataclasses import dataclass
import json
from importlib.resources import files

FULL_POINTS = 2


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


def load_bench() -> Bench:
    """Load the built-in fixture bench shipped with the package."""
    raw = json.loads(files("agit").joinpath("fixtures.json").read_text(encoding="utf-8"))
    criteria: list[RubricCriterion] = []
    for item in raw["rubric"]:
        max_points = item["max_points"]
        assert max_points == FULL_POINTS
        criteria.append(
            RubricCriterion(
                criterion_id=item["id"],
                max_points=max_points,
                text=item["text"],
            )
        )
    criterion_ids = [criterion.criterion_id for criterion in criteria]
    cases: list[BenchCase] = []
    for item in raw["cases"]:
        checks: dict[str, tuple[str, ...]] = {}
        for criterion_id in criterion_ids:
            needles = item["checks"][criterion_id]
            assert isinstance(needles, list) and len(needles) >= 1
            assert all(isinstance(needle, str) and needle != "" for needle in needles)
            checks[criterion_id] = tuple(needles)
        cases.append(BenchCase(case_id=item["id"], task=item["task"], checks=checks))
    assert len(criteria) >= 1 and len(cases) >= 1
    return Bench(criteria=tuple(criteria), cases=tuple(cases))
