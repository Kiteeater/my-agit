"""Score a skill and prompt with the offline stub or an OpenAI-compatible judge."""

import json
import os
from dataclasses import dataclass
from urllib import error, request

from agit.bench import FULL_POINTS, Bench, BenchCase, RubricCriterion

ENV_BASE_URL = "AGIT_JUDGE_BASE_URL"
ENV_API_KEY = "AGIT_JUDGE_API_KEY"
ENV_MODEL = "AGIT_JUDGE_MODEL"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
JUDGE_TIMEOUT_SECONDS = 60
JUDGE_MAX_TOKENS = 400

SYSTEM_PROMPT = (
    "You score an agent harness, which is a skill document plus a system prompt, "
    "against one benchmark case. Reply with a single JSON object and no markdown. "
    "The object has a scores field and a notes field. scores maps each rubric "
    "criterion id to the integer 0, 1, or 2. 0 misses the criterion, 1 partially "
    "meets it, and 2 meets it. notes is one short sentence."
)


class AgitError(Exception):
    """User-facing failure with an HTTP status for the API adapter."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(kw_only=True)
class CriterionScore:
    criterion_id: str
    points: int
    max_points: int


@dataclass(kw_only=True)
class CaseScore:
    case_id: str
    points: int
    max_points: int
    note: str
    criteria: list[CriterionScore]


@dataclass(kw_only=True)
class HarnessScore:
    judge_name: str
    model: str | None
    points: int
    max_points: int
    cases: list[CaseScore]

    def to_dict(self, version_id: str) -> dict[str, object]:
        return {
            "version_id": version_id,
            "judge_name": self.judge_name,
            "model": self.model,
            "points": self.points,
            "max_points": self.max_points,
            "score": self.points / self.max_points,
            "cases": [
                {
                    "case_id": case.case_id,
                    "points": case.points,
                    "max_points": case.max_points,
                    "note": case.note,
                    "criteria": [
                        {
                            "criterion_id": criterion.criterion_id,
                            "points": criterion.points,
                            "max_points": criterion.max_points,
                        }
                        for criterion in case.criteria
                    ],
                }
                for case in self.cases
            ],
        }


def make_judge(mode: str) -> "StubJudge | OpenAIJudge":
    """Pick the stub, or the HTTP judge when an API key is configured."""
    if mode == "stub":
        return StubJudge()
    if mode != "auto":
        raise AgitError("judge must be auto or stub", 400)
    api_key = os.environ.get(ENV_API_KEY, "").strip()
    if api_key == "":
        return StubJudge()
    configured_base_url = os.environ.get(ENV_BASE_URL, "").strip()
    base_url = configured_base_url if configured_base_url != "" else DEFAULT_BASE_URL
    configured_model = os.environ.get(ENV_MODEL, "").strip()
    model = configured_model if configured_model != "" else DEFAULT_MODEL
    return OpenAIJudge(base_url=base_url, api_key=api_key, model=model)


class StubJudge:
    """Score fixture phrases so the gate runs with no network and no API key.

    All phrases for a criterion score 2, some score 1, and none score 0.
    """

    name = "stub"
    model = None

    def score_harness(self, skill: str, prompt: str, bench: Bench) -> HarnessScore:
        haystack = (skill + "\n" + prompt).casefold()
        cases = [self.score_case(haystack, case, bench.criteria) for case in bench.cases]
        return HarnessScore(
            judge_name=self.name,
            model=self.model,
            points=sum(case.points for case in cases),
            max_points=sum(case.max_points for case in cases),
            cases=cases,
        )

    def score_case(
        self,
        haystack: str,
        case: BenchCase,
        criteria: tuple[RubricCriterion, ...],
    ) -> CaseScore:
        criterion_scores: list[CriterionScore] = []
        missed: list[str] = []
        for criterion in criteria:
            needles = case.checks[criterion.criterion_id]
            found = sum(1 for needle in needles if needle.casefold() in haystack)
            if found == len(needles):
                points = FULL_POINTS
            elif found == 0:
                points = 0
            else:
                points = FULL_POINTS // 2
            criterion_scores.append(
                CriterionScore(
                    criterion_id=criterion.criterion_id,
                    points=points,
                    max_points=criterion.max_points,
                )
            )
            missing = [needle for needle in needles if needle.casefold() not in haystack]
            if len(missing) > 0:
                missed.append(criterion.criterion_id + ": " + ", ".join(missing))
        if len(missed) == 0:
            note = "all fixture phrases matched"
        else:
            note = "missing " + "; ".join(missed)
        return CaseScore(
            case_id=case.case_id,
            points=sum(item.points for item in criterion_scores),
            max_points=sum(item.max_points for item in criterion_scores),
            note=note,
            criteria=criterion_scores,
        )


class OpenAIJudge:
    """Chat-completions client. base_url is the API root, including /v1 when required."""

    name = "openai-compatible"

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def score_harness(self, skill: str, prompt: str, bench: Bench) -> HarnessScore:
        cases = [self.score_case(skill, prompt, case, bench.criteria) for case in bench.cases]
        return HarnessScore(
            judge_name=self.name,
            model=self.model,
            points=sum(case.points for case in cases),
            max_points=sum(case.max_points for case in cases),
            cases=cases,
        )

    def score_case(
        self,
        skill: str,
        prompt: str,
        case: BenchCase,
        criteria: tuple[RubricCriterion, ...],
    ) -> CaseScore:
        content = self.complete(user_prompt(skill, prompt, case, criteria))
        points_by_id, note = parse_judge_content(content, tuple(criterion.criterion_id for criterion in criteria))
        criterion_scores = [
            CriterionScore(
                criterion_id=criterion.criterion_id,
                points=points_by_id[criterion.criterion_id],
                max_points=criterion.max_points,
            )
            for criterion in criteria
        ]
        return CaseScore(
            case_id=case.case_id,
            points=sum(item.points for item in criterion_scores),
            max_points=sum(item.max_points for item in criterion_scores),
            note=note,
            criteria=criterion_scores,
        )

    def complete(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": JUDGE_MAX_TOKENS,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        http_request = request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=JUDGE_TIMEOUT_SECONDS) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as http_error:
            detail = http_error.read().decode("utf-8", errors="replace")
            raise AgitError(
                f"judge request failed: HTTP {http_error.code} {detail}",
                502,
            ) from http_error
        except error.URLError as url_error:
            raise AgitError(f"judge request failed: {url_error.reason}", 502) from url_error
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as decode_error:
            raise AgitError("judge response was not JSON", 502) from decode_error
        return chat_message_content(parsed)


def user_prompt(
    skill: str,
    prompt: str,
    case: BenchCase,
    criteria: tuple[RubricCriterion, ...],
) -> str:
    lines = ["Rubric:"]
    for criterion in criteria:
        lines.append(f"- {criterion.criterion_id} (0-2): {criterion.text}")
    lines.extend(["", "Case:", case.task, "", "Skill:", skill, "", "Prompt:", prompt])
    return "\n".join(lines)


def chat_message_content(payload: object) -> str:
    if not isinstance(payload, dict):
        raise AgitError("judge response was not a JSON object", 502)
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) == 0 or not isinstance(choices[0], dict):
        raise AgitError("judge response missing choices", 502)
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise AgitError("judge response missing message", 502)
    content = message.get("content")
    if not isinstance(content, str) or content.strip() == "":
        raise AgitError("judge response missing content", 502)
    return content


def parse_judge_content(content: str, criterion_ids: tuple[str, ...]) -> tuple[dict[str, int], str]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if len(lines) > 0 and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as decode_error:
        raise AgitError("judge content was not JSON", 502) from decode_error
    if not isinstance(payload, dict) or not isinstance(payload.get("scores"), dict):
        raise AgitError("judge content missing scores", 502)
    scores = payload["scores"]
    notes = payload.get("notes", "")
    if not isinstance(notes, str):
        raise AgitError("judge notes must be a string", 502)
    parsed: dict[str, int] = {}
    for criterion_id in criterion_ids:
        parsed[criterion_id] = coerce_point(scores.get(criterion_id), criterion_id)
    return parsed, notes


def coerce_point(value: object, criterion_id: str) -> int:
    point: int | None
    if isinstance(value, bool):
        point = None
    elif isinstance(value, int) and value in (0, 1, 2):
        point = value
    elif isinstance(value, float) and value in (0.0, 1.0, 2.0):
        point = int(value)
    else:
        point = None
    if point is None:
        raise AgitError(f"judge score for {criterion_id} must be 0, 1, or 2", 502)
    return point
