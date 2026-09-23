"""Vertical slice: auth, versions, the release gate, judges, and benches."""

import hashlib
import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agit.api import build_server
from agit.bench import load_bench
from agit.core import (
    bootstrap_black,
    compare_red_black,
    create_project,
    gate_passes,
    get_project,
    list_versions,
    mark_red,
    push_version,
    run_gate,
    version_id_for,
)
from agit.demo import example_text
from agit.domain import Harness
from agit.judge import AgitError, FixedJudge, OpenAIJudge, StubJudge, make_judge
from agit.store import Store

ROOT = Path(__file__).resolve().parents[1]
GIT_REMOTE_URL = "git@github.com:acme/support-agent.git"


class SliceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "store.json")

    def test_create_binds_git_remote_and_stores_key_hash_only(self) -> None:
        created = create_project(self.store, "support-agent", GIT_REMOTE_URL)
        api_key = str(created["api_key"])
        self.assertTrue(api_key.startswith("agk_"))
        self.assertEqual(created["git_remote_url"], GIT_REMOTE_URL)
        disk = self.store.path.read_text(encoding="utf-8")
        self.assertNotIn(api_key, disk)
        self.assertIn(hashlib.sha256(api_key.encode("utf-8")).hexdigest(), disk)
        with self.assertRaises(AgitError) as caught:
            create_project(self.store, "other", "not a remote")
        self.assertEqual(caught.exception.status_code, 400)
        https_project = create_project(
            self.store,
            "https-remote",
            "https://github.com/acme/support-agent.git",
        )
        self.assertEqual(https_project["git_remote_url"], "https://github.com/acme/support-agent.git")

    def test_version_id_is_skill_prompt_hash_and_repush_keeps_it(self) -> None:
        created = create_project(self.store, "support-agent", GIT_REMOTE_URL)
        project_id = str(created["project_id"])
        api_key = str(created["api_key"])
        skill = "skill-text"
        prompt = "prompt-text"
        legacy_id = hashlib.sha256(skill.encode("utf-8") + b"\0" + prompt.encode("utf-8")).hexdigest()
        pushed = push_version(self.store, project_id, api_key, skill, prompt, "first")
        self.assertEqual(pushed["version_id"], legacy_id)
        self.assertEqual(pushed["version_id"], version_id_for(Harness(skill=skill, prompt=prompt)))
        self.assertNotIn("tool", pushed)
        self.assertTrue(pushed["created"])
        again = push_version(self.store, project_id, api_key, skill, prompt, "second message")
        self.assertFalse(again["created"])
        self.assertEqual(again["version_id"], pushed["version_id"])
        self.assertEqual(again["message"], "first")
        listed = list_versions(self.store, project_id, api_key)
        versions = listed["versions"]
        self.assertIsInstance(versions, list)
        self.assertEqual(len(versions), 1)
        reopened = list_versions(Store(self.store.path), project_id, api_key)
        self.assertEqual(reopened, listed)
        with self.assertRaises(AgitError) as bad_key:
            push_version(self.store, project_id, "agk_wrong", skill, prompt, "nope")
        self.assertEqual(bad_key.exception.status_code, 401)
        with self.assertRaises(AgitError) as empty:
            push_version(self.store, project_id, api_key, "  ", prompt, "empty")
        self.assertEqual(empty.exception.status_code, 400)

    def test_old_store_json_loads_and_tool_slot_changes_only_its_own_id(self) -> None:
        skill = "legacy-skill"
        prompt = "legacy-prompt"
        version_id = hashlib.sha256(skill.encode("utf-8") + b"\0" + prompt.encode("utf-8")).hexdigest()
        api_key = "agk_old"
        record = {
            "projects": {
                "proj_old": {
                    "api_key_sha256": hashlib.sha256(api_key.encode("utf-8")).hexdigest(),
                    "black_version_id": None,
                    "created_at": "2020-01-01T00:00:00Z",
                    "git_remote_url": GIT_REMOTE_URL,
                    "name": "old",
                    "project_id": "proj_old",
                    "red_version_id": None,
                    "releases": [],
                    "versions": {
                        version_id: {
                            "created_at": "2020-01-01T00:00:00Z",
                            "message": "legacy",
                            "prompt": prompt,
                            "skill": skill,
                            "version_id": version_id,
                        }
                    },
                }
            }
        }
        self.store.path.write_text(json.dumps(record), encoding="utf-8")
        loaded = self.store.read()["proj_old"].versions[version_id]
        self.assertEqual(loaded.harness.skill, skill)
        self.assertEqual(loaded.harness.prompt, prompt)
        self.assertIsNone(loaded.harness.tool)
        listed = list_versions(self.store, "proj_old", api_key)
        versions = listed["versions"]
        self.assertIsInstance(versions, list)
        self.assertNotIn("tool", versions[0])
        self.assertEqual(versions[0]["version_id"], version_id)

        created = create_project(self.store, "support-agent", GIT_REMOTE_URL)
        rewritten = json.loads(self.store.path.read_text(encoding="utf-8"))
        legacy_version = rewritten["projects"]["proj_old"]["versions"][version_id]
        self.assertEqual(set(legacy_version.keys()), {"version_id", "skill", "prompt", "message", "created_at"})

        project_id = str(created["project_id"])
        project_key = str(created["api_key"])
        with_tool = push_version(self.store, project_id, project_key, skill, prompt, "tool slot", tool="echo")
        self.assertEqual(with_tool["tool"], "echo")
        self.assertNotEqual(with_tool["version_id"], version_id)
        extended_id = hashlib.sha256(
            b"skill\0" + skill.encode("utf-8") + b"\0prompt\0" + prompt.encode("utf-8") + b"\0tool\0" + b"echo"
        ).hexdigest()
        self.assertEqual(with_tool["version_id"], extended_id)
        self.assertEqual(with_tool["version_id"], version_id_for(Harness(skill=skill, prompt=prompt, tool="echo")))
        stored = Store(self.store.path).read()[project_id].versions[str(with_tool["version_id"])]
        self.assertEqual(stored.harness.tool, "echo")

    def test_stub_scores_and_gate_rejects_tie_weaker_then_promotes_stronger(self) -> None:
        bench = load_bench()
        judge = StubJudge()
        baseline_skill = example_text("baseline_skill.md")
        baseline_prompt = example_text("baseline_prompt.md")
        baseline = judge.score_harness(Harness(skill=baseline_skill, prompt=baseline_prompt), bench)
        weaker = judge.score_harness(
            Harness(skill=example_text("weaker_skill.md"), prompt=example_text("weaker_prompt.md")),
            bench,
        )
        stronger = judge.score_harness(
            Harness(skill=example_text("stronger_skill.md"), prompt=example_text("stronger_prompt.md")),
            bench,
        )
        tied = judge.score_harness(
            Harness(skill=baseline_skill, prompt=baseline_prompt + "\nThanks for the review.\n"),
            bench,
        )
        self.assertEqual((baseline.points, weaker.points, stronger.points, tied.points), (12, 0, 18, 12))
        self.assertEqual(judge.score_harness(Harness(skill="rename", prompt=""), bench).points, 1)
        self.assertEqual(
            judge.score_harness(Harness(skill="no", prompt="no", tool="rename call site"), bench).points,
            0,
        )
        self.assertEqual(baseline.max_points, 18)

        created = create_project(self.store, "support-agent", GIT_REMOTE_URL)
        project_id = str(created["project_id"])
        api_key = str(created["api_key"])
        baseline_version = push_version(self.store, project_id, api_key, baseline_skill, baseline_prompt, "baseline")
        baseline_id = str(baseline_version["version_id"])
        bootstrap_black(self.store, project_id, api_key, baseline_id)
        with self.assertRaises(AgitError) as second_black:
            bootstrap_black(self.store, project_id, api_key, baseline_id)
        self.assertEqual(second_black.exception.status_code, 409)
        with self.assertRaises(AgitError) as same_red:
            mark_red(self.store, project_id, api_key, baseline_id)
        self.assertEqual(same_red.exception.status_code, 409)

        tied_version = push_version(
            self.store,
            project_id,
            api_key,
            baseline_skill,
            baseline_prompt + "\nThanks for the review.\n",
            "tie",
        )
        mark_red(self.store, project_id, api_key, str(tied_version["version_id"]))
        tie_gate = run_gate(self.store, project_id, api_key, 0.0, judge)
        self.assertFalse(tie_gate["promoted"])
        self.assertEqual(tie_gate["black_version_id"], baseline_id)

        weaker_version = push_version(
            self.store,
            project_id,
            api_key,
            example_text("weaker_skill.md"),
            example_text("weaker_prompt.md"),
            "weaker",
        )
        mark_red(self.store, project_id, api_key, str(weaker_version["version_id"]))
        weak_gate = run_gate(self.store, project_id, api_key, 0.0, judge)
        self.assertFalse(weak_gate["promoted"])
        self.assertEqual(weak_gate["black_version_id"], baseline_id)
        self.assertEqual(weak_gate["red_version_id"], weaker_version["version_id"])

        stronger_version = push_version(
            self.store,
            project_id,
            api_key,
            example_text("stronger_skill.md"),
            example_text("stronger_prompt.md"),
            "stronger",
        )
        stronger_id = str(stronger_version["version_id"])
        mark_red(self.store, project_id, api_key, stronger_id)
        short_lead = run_gate(self.store, project_id, api_key, 0.5, judge)
        self.assertFalse(short_lead["promoted"])
        self.assertEqual(short_lead["black_version_id"], baseline_id)
        promoted = run_gate(self.store, project_id, api_key, 0.2, judge)
        self.assertTrue(promoted["promoted"])
        self.assertEqual(promoted["black_version_id"], stronger_id)
        self.assertIsNone(promoted["red_version_id"])
        self.assertEqual(get_project(self.store, project_id, api_key)["black_version_id"], stronger_id)
        with self.assertRaises(AgitError) as negative:
            run_gate(self.store, project_id, api_key, -0.1, judge)
        self.assertEqual(negative.exception.status_code, 400)

    def test_fixed_judge_differs_from_stub_and_gate_keeps_its_rule(self) -> None:
        bench = load_bench()
        zero_harness = Harness(skill="aa", prompt="b")
        lead_harness = Harness(skill="a", prompt="b")
        half_harness = Harness(skill="ab", prompt="cd")
        stub = make_judge("stub")
        fixed = make_judge("fixed")
        self.assertIsInstance(stub, StubJudge)
        self.assertIsInstance(fixed, FixedJudge)
        stub_lead = stub.score_harness(lead_harness, bench)
        fixed_lead = fixed.score_harness(lead_harness, bench)
        fixed_again = fixed.score_harness(lead_harness, bench)
        fixed_zero = fixed.score_harness(zero_harness, bench)
        fixed_half = fixed.score_harness(half_harness, bench)
        fixed_with_tool = fixed.score_harness(
            Harness(skill=lead_harness.skill, prompt=lead_harness.prompt, tool="rename call site"),
            bench,
        )
        self.assertEqual(fixed_lead.judge_name, "fixed")
        self.assertIsNone(fixed_lead.model)
        self.assertEqual(fixed_lead.points, fixed_lead.max_points)
        self.assertEqual(fixed_lead.cases[0].note, "length band 2")
        self.assertEqual(fixed_again.points, fixed_lead.points)
        self.assertEqual(fixed_again.cases[0].note, fixed_lead.cases[0].note)
        self.assertEqual(fixed_with_tool.points, fixed_lead.points)
        self.assertEqual(fixed_zero.points, 0)
        self.assertEqual(fixed_zero.cases[0].note, "length band 0")
        self.assertEqual(fixed_half.points * 2, fixed_half.max_points)
        self.assertEqual(fixed_half.cases[0].note, "length band 1")
        self.assertNotEqual(fixed_lead.points, stub_lead.points)
        self.assertNotEqual(fixed_lead.judge_name, stub_lead.judge_name)
        self.assertFalse(
            gate_passes(stub_lead.points, stub.score_harness(zero_harness, bench).points, stub_lead.max_points, 0.0)
        )
        with patch.dict(os.environ, {"AGIT_JUDGE_API_KEY": "test-key"}, clear=False):
            self.assertIsInstance(make_judge("fixed"), FixedJudge)
            self.assertIsInstance(make_judge("auto"), OpenAIJudge)
        with self.assertRaises(AgitError) as unknown:
            make_judge("nope")
        self.assertEqual(unknown.exception.status_code, 400)
        self.assertIn("nope", unknown.exception.message)

        created = create_project(self.store, "support-agent", GIT_REMOTE_URL)
        project_id = str(created["project_id"])
        api_key = str(created["api_key"])
        black_version = push_version(self.store, project_id, api_key, "aa", "b", "tie black")
        black_id = str(black_version["version_id"])
        bootstrap_black(self.store, project_id, api_key, black_id)
        tie_version = push_version(self.store, project_id, api_key, "a", "bc", "tie red")
        tie_id = str(tie_version["version_id"])
        mark_red(self.store, project_id, api_key, tie_id)
        tie_compare = compare_red_black(self.store, project_id, api_key, fixed)
        self.assertEqual(tie_compare["judge_name"], "fixed")
        self.assertEqual(tie_compare["black_points"], 0)
        self.assertEqual(tie_compare["red_points"], 0)
        tie_gate = run_gate(self.store, project_id, api_key, 0.0, fixed)
        self.assertFalse(tie_gate["promoted"])
        self.assertEqual(tie_gate["judge_name"], "fixed")
        self.assertEqual(tie_gate["black_version_id"], black_id)
        self.assertEqual(tie_gate["red_version_id"], tie_id)
        lead_version = push_version(self.store, project_id, api_key, "a", "b", "lead")
        lead_id = str(lead_version["version_id"])
        mark_red(self.store, project_id, api_key, lead_id)
        lead_gate = run_gate(self.store, project_id, api_key, 0.0, fixed)
        self.assertTrue(lead_gate["promoted"])
        self.assertEqual(lead_gate["judge_name"], "fixed")
        self.assertEqual(lead_gate["black_version_id"], lead_id)
        self.assertIsNone(lead_gate["red_version_id"])

    def test_cli_and_http_accept_fixed_and_reject_unknown_judge(self) -> None:
        created = self.run_agit("project", "create", "--name", "demo", "--git-url", GIT_REMOTE_URL)
        project_id = str(created["project_id"])
        api_key = str(created["api_key"])
        auth = ["--project", project_id, "--key", api_key]
        skill_path = Path(self.temporary.name) / "skill.txt"
        prompt_path = Path(self.temporary.name) / "prompt.txt"
        lead_prompt_path = Path(self.temporary.name) / "lead-prompt.txt"
        skill_path.write_text("aa", encoding="utf-8")
        prompt_path.write_text("b", encoding="utf-8")
        lead_prompt_path.write_text("b", encoding="utf-8")
        black = self.run_agit(
            "version",
            "push",
            *auth,
            "--skill-file",
            str(skill_path),
            "--prompt-file",
            str(prompt_path),
            "--message",
            "black",
        )
        self.run_agit("release", "black", *auth, "--version", str(black["version_id"]))
        skill_path.write_text("a", encoding="utf-8")
        red = self.run_agit(
            "version",
            "push",
            *auth,
            "--skill-file",
            str(skill_path),
            "--prompt-file",
            str(lead_prompt_path),
            "--message",
            "red",
        )
        self.run_agit("release", "red", *auth, "--version", str(red["version_id"]))
        compared = self.run_agit("compare", *auth, "--judge", "fixed")
        self.assertEqual(compared["judge_name"], "fixed")
        self.assertEqual(compared["max_points"], 18)
        self.assertGreater(int(compared["red_points"]), int(compared["black_points"]))
        gated = self.run_agit("gate", *auth, "--judge", "fixed", "--margin", "0")
        self.assertEqual(gated["judge_name"], "fixed")
        self.assertTrue(gated["promoted"])
        self.assertEqual(gated["black_version_id"], red["version_id"])
        unknown = subprocess.run(
            [
                sys.executable,
                "-m",
                "agit",
                "--store",
                str(self.store.path),
                "compare",
                *auth,
                "--judge",
                "nope",
            ],
            cwd=ROOT,
            env={key: value for key, value in os.environ.items() if key != "AGIT_JUDGE_API_KEY"},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn("fixed", unknown.stderr)

        with running(build_server("127.0.0.1", 0, self.store.path)) as server:
            port = server.server_address[1]
            base = f"http://127.0.0.1:{port}"
            missing_status, missing = call(
                "POST",
                f"{base}/v1/projects/{project_id}/compare",
                {"judge": "nope"},
                api_key=api_key,
            )
            self.assertEqual(missing_status, 400)
            self.assertIn("unknown judge", str(missing["error"]))
            created_status, http_created = call(
                "POST",
                base + "/v1/projects",
                {"name": "support-agent", "git_remote_url": GIT_REMOTE_URL},
            )
            self.assertEqual(created_status, 201)
            http_project_id = str(http_created["project_id"])
            http_key = str(http_created["api_key"])
            black_status, http_black = call(
                "POST",
                f"{base}/v1/projects/{http_project_id}/versions",
                {"skill": "aa", "prompt": "b", "message": "black"},
                api_key=http_key,
            )
            self.assertEqual(black_status, 200)
            live_status, _live = call(
                "POST",
                f"{base}/v1/projects/{http_project_id}/black",
                {"version_id": http_black["version_id"]},
                api_key=http_key,
            )
            self.assertEqual(live_status, 200)
            red_status, http_red = call(
                "POST",
                f"{base}/v1/projects/{http_project_id}/versions",
                {"skill": "a", "prompt": "b", "message": "red"},
                api_key=http_key,
            )
            self.assertEqual(red_status, 200)
            mark_status, _marked = call(
                "POST",
                f"{base}/v1/projects/{http_project_id}/red",
                {"version_id": http_red["version_id"]},
                api_key=http_key,
            )
            self.assertEqual(mark_status, 200)
            gate_status, http_gate = call(
                "POST",
                f"{base}/v1/projects/{http_project_id}/gate",
                {"margin": 0, "judge": "fixed"},
                api_key=http_key,
            )
            self.assertEqual(gate_status, 200)
            self.assertEqual(http_gate["judge_name"], "fixed")
            self.assertEqual(http_gate["max_points"], 18)
            self.assertTrue(http_gate["promoted"])
            self.assertEqual(http_gate["black_version_id"], http_red["version_id"])

    def test_missing_judge_key_uses_stub_and_http_judge_parses_fenced_json(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsInstance(make_judge("auto"), StubJudge)
            self.assertIsInstance(make_judge("stub"), StubJudge)
        with patch.dict(
            os.environ,
            {
                "AGIT_JUDGE_API_KEY": "test-key",
                "AGIT_JUDGE_MODEL": "fake-model",
                "AGIT_JUDGE_BASE_URL": "http://example.test/v1",
            },
            clear=False,
        ):
            selected = make_judge("auto")
            self.assertIsInstance(selected, OpenAIJudge)
            self.assertEqual(selected.model, "fake-model")
            self.assertEqual(selected.base_url, "http://example.test/v1")
        with patch.dict(os.environ, {"AGIT_JUDGE_API_KEY": "  "}, clear=False):
            self.assertIsInstance(make_judge("auto"), StubJudge)

        with running(fake_judge_server()) as server:
            port = server.server_address[1]
            judge = OpenAIJudge(
                base_url=f"http://127.0.0.1:{port}/v1",
                api_key="test-key",
                model="fake-model",
            )
            score = judge.score_harness(Harness(skill="skill", prompt="prompt"), load_bench())
        self.assertEqual(score.judge_name, "openai-compatible")
        self.assertEqual(score.points, 15)
        self.assertEqual(score.max_points, 18)
        self.assertEqual(score.cases[0].note, "partial concreteness")

    def test_http_auth_versions_and_gate(self) -> None:
        with running(build_server("127.0.0.1", 0, self.store.path)) as server:
            port = server.server_address[1]
            base = f"http://127.0.0.1:{port}"
            missing_status, missing_body = call("GET", base + "/v1/nope")
            self.assertEqual(missing_status, 404)
            self.assertEqual(missing_body["error"], "not found")
            created_status, created = call(
                "POST",
                base + "/v1/projects",
                {"name": "support-agent", "git_remote_url": GIT_REMOTE_URL},
            )
            self.assertEqual(created_status, 201)
            project_id = created["project_id"]
            api_key = created["api_key"]
            denied_status, denied = call(
                "POST",
                f"{base}/v1/projects/{project_id}/versions",
                {"skill": "s", "prompt": "p", "message": "m"},
            )
            self.assertEqual(denied_status, 401)
            self.assertIn("error", denied)
            pushed_status, pushed = call(
                "POST",
                f"{base}/v1/projects/{project_id}/versions",
                {
                    "skill": example_text("baseline_skill.md"),
                    "prompt": example_text("baseline_prompt.md"),
                    "message": "baseline",
                },
                api_key=api_key,
            )
            self.assertEqual(pushed_status, 200)
            black_status, _black = call(
                "POST",
                f"{base}/v1/projects/{project_id}/black",
                {"version_id": pushed["version_id"]},
                api_key=api_key,
            )
            self.assertEqual(black_status, 200)
            stronger_status, stronger = call(
                "POST",
                f"{base}/v1/projects/{project_id}/versions",
                {
                    "skill": example_text("stronger_skill.md"),
                    "prompt": example_text("stronger_prompt.md"),
                    "message": "stronger",
                },
                api_key=api_key,
            )
            self.assertEqual(stronger_status, 200)
            red_status, _red = call(
                "POST",
                f"{base}/v1/projects/{project_id}/red",
                {"version_id": stronger["version_id"]},
                api_key=api_key,
            )
            self.assertEqual(red_status, 200)
            gate_status, gate = call(
                "POST",
                f"{base}/v1/projects/{project_id}/gate",
                {"margin": 0, "judge": "stub"},
                api_key=api_key,
            )
            self.assertEqual(gate_status, 200)
            self.assertTrue(gate["promoted"])
            self.assertEqual(gate["black_version_id"], stronger["version_id"])
            self.assertIsNone(gate["red_version_id"])
            release_status, releases = call(
                "GET",
                f"{base}/v1/projects/{project_id}/releases",
                api_key=api_key,
            )
            self.assertEqual(release_status, 200)
            self.assertEqual(
                [event["action"] for event in releases["releases"]],
                ["bootstrap_black", "set_red", "promote"],
            )

    def test_cli_demo_and_manual_gate(self) -> None:
        demo = subprocess.run(
            [sys.executable, "-m", "agit", "demo"],
            cwd=ROOT,
            env={
                **os.environ,
                "AGIT_JUDGE_API_KEY": "should-not-be-called",
                "AGIT_JUDGE_BASE_URL": "http://127.0.0.1:9/v1",
            },
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(demo.returncode, 0, demo.stderr)
        demo_payload = json.loads(demo.stdout)
        self.assertEqual(demo_payload["judge_name"], "stub")
        self.assertFalse(demo_payload["weaker"]["promoted"])
        self.assertEqual(demo_payload["weaker"]["black_version_id"], demo_payload["baseline_version_id"])
        self.assertTrue(demo_payload["stronger"]["promoted"])
        self.assertEqual(demo_payload["stronger"]["tool"], "echo")
        self.assertEqual(demo_payload["final_black_version_id"], demo_payload["stronger"]["version_id"])
        self.assertIsNone(demo_payload["final_red_version_id"])
        self.assertEqual(
            demo_payload["release_actions"],
            ["bootstrap_black", "set_red", "reject", "set_red", "promote"],
        )

        created = self.run_agit("project", "create", "--name", "demo", "--git-url", GIT_REMOTE_URL)
        project_id = created["project_id"]
        api_key = created["api_key"]
        auth = ["--project", project_id, "--key", api_key]
        baseline = self.run_agit(
            "version",
            "push",
            *auth,
            "--skill-file",
            str(ROOT / "examples" / "baseline_skill.md"),
            "--prompt-file",
            str(ROOT / "examples" / "baseline_prompt.md"),
            "--message",
            "baseline",
        )
        self.run_agit("release", "black", *auth, "--version", baseline["version_id"])
        stronger = self.run_agit(
            "version",
            "push",
            *auth,
            "--skill-file",
            str(ROOT / "examples" / "stronger_skill.md"),
            "--prompt-file",
            str(ROOT / "examples" / "stronger_prompt.md"),
            "--message",
            "stronger",
        )
        self.run_agit("release", "red", *auth, "--version", stronger["version_id"])
        gate = self.run_agit("gate", *auth, "--judge", "stub", "--margin", "0")
        self.assertTrue(gate["promoted"])
        self.assertEqual(gate["black_version_id"], stronger["version_id"])
        listed = self.run_agit("version", "list", *auth)
        versions = listed["versions"]
        self.assertIsInstance(versions, list)
        self.assertEqual(len(versions), 2)

        rejected = subprocess.run(
            [sys.executable, "-m", "agit", "--store", str(self.store.path), "project", "create", "--name", "bad", "--git-url", "nope"],
            cwd=ROOT,
            env={key: value for key, value in os.environ.items() if key != "AGIT_JUDGE_API_KEY"},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("git_remote_url", rejected.stderr)

    def test_cli_push_tool_file_matches_biz_and_omit_keeps_shape(self) -> None:
        skill_path = Path(self.temporary.name) / "skill.txt"
        prompt_path = Path(self.temporary.name) / "prompt.txt"
        tool_path = Path(self.temporary.name) / "tool.txt"
        skill = "skill-text"
        prompt = "prompt-text"
        tool = "echo"
        skill_path.write_text(skill, encoding="utf-8")
        prompt_path.write_text(prompt, encoding="utf-8")
        tool_path.write_text(tool, encoding="utf-8")
        created = self.run_agit("project", "create", "--name", "demo", "--git-url", GIT_REMOTE_URL)
        project_id = str(created["project_id"])
        api_key = str(created["api_key"])
        auth = ["--project", project_id, "--key", api_key]
        plain = self.run_agit(
            "version",
            "push",
            *auth,
            "--skill-file",
            str(skill_path),
            "--prompt-file",
            str(prompt_path),
            "--message",
            "plain",
        )
        self.assertNotIn("tool", plain)
        self.assertEqual(plain["version_id"], version_id_for(Harness(skill=skill, prompt=prompt)))
        self.assertTrue(plain["created"])
        plain_again = self.run_agit(
            "version",
            "push",
            *auth,
            "--skill-file",
            str(skill_path),
            "--prompt-file",
            str(prompt_path),
            "--message",
            "plain again",
        )
        self.assertFalse(plain_again["created"])
        self.assertEqual(plain_again["version_id"], plain["version_id"])
        self.assertNotIn("tool", plain_again)
        with_tool = self.run_agit(
            "version",
            "push",
            *auth,
            "--skill-file",
            str(skill_path),
            "--prompt-file",
            str(prompt_path),
            "--tool-file",
            str(tool_path),
            "--message",
            "with tool",
        )
        self.assertEqual(with_tool["tool"], tool)
        self.assertEqual(with_tool["version_id"], version_id_for(Harness(skill=skill, prompt=prompt, tool=tool)))
        self.assertNotEqual(with_tool["version_id"], plain["version_id"])
        self.assertTrue(with_tool["created"])
        tool_again = self.run_agit(
            "version",
            "push",
            *auth,
            "--skill-file",
            str(skill_path),
            "--prompt-file",
            str(prompt_path),
            "--tool-file",
            str(tool_path),
            "--message",
            "with tool again",
        )
        self.assertFalse(tool_again["created"])
        self.assertEqual(tool_again["version_id"], with_tool["version_id"])
        self.assertEqual(tool_again["message"], "with tool")

    def test_http_push_tool_matches_biz_and_omit_keeps_shape(self) -> None:
        skill = "skill-text"
        prompt = "prompt-text"
        tool = "echo"
        with running(build_server("127.0.0.1", 0, self.store.path)) as server:
            port = server.server_address[1]
            base = f"http://127.0.0.1:{port}"
            created_status, created = call(
                "POST",
                base + "/v1/projects",
                {"name": "support-agent", "git_remote_url": GIT_REMOTE_URL},
            )
            self.assertEqual(created_status, 201)
            project_id = created["project_id"]
            api_key = str(created["api_key"])
            plain_status, plain = call(
                "POST",
                f"{base}/v1/projects/{project_id}/versions",
                {"skill": skill, "prompt": prompt, "message": "plain"},
                api_key=api_key,
            )
            self.assertEqual(plain_status, 200)
            self.assertNotIn("tool", plain)
            self.assertEqual(plain["version_id"], version_id_for(Harness(skill=skill, prompt=prompt)))
            self.assertTrue(plain["created"])
            again_status, plain_again = call(
                "POST",
                f"{base}/v1/projects/{project_id}/versions",
                {"skill": skill, "prompt": prompt, "message": "plain again"},
                api_key=api_key,
            )
            self.assertEqual(again_status, 200)
            self.assertFalse(plain_again["created"])
            self.assertEqual(plain_again["version_id"], plain["version_id"])
            self.assertNotIn("tool", plain_again)
            tool_status, with_tool = call(
                "POST",
                f"{base}/v1/projects/{project_id}/versions",
                {"skill": skill, "prompt": prompt, "message": "with tool", "tool": tool},
                api_key=api_key,
            )
            self.assertEqual(tool_status, 200)
            self.assertEqual(with_tool["tool"], tool)
            self.assertEqual(with_tool["version_id"], version_id_for(Harness(skill=skill, prompt=prompt, tool=tool)))
            self.assertNotEqual(with_tool["version_id"], plain["version_id"])
            self.assertTrue(with_tool["created"])
            repush_status, tool_again = call(
                "POST",
                f"{base}/v1/projects/{project_id}/versions",
                {"skill": skill, "prompt": prompt, "message": "with tool again", "tool": tool},
                api_key=api_key,
            )
            self.assertEqual(repush_status, 200)
            self.assertFalse(tool_again["created"])
            self.assertEqual(tool_again["version_id"], with_tool["version_id"])
            self.assertEqual(tool_again["message"], "with tool")
            bad_status, bad = call(
                "POST",
                f"{base}/v1/projects/{project_id}/versions",
                {"skill": skill, "prompt": prompt, "message": "bad tool", "tool": 1},
                api_key=api_key,
            )
            self.assertEqual(bad_status, 400)
            self.assertIn("error", bad)

    def test_load_bench_reads_fixture_hot_and_a_json_file(self) -> None:
        for spec, filename in (("fixture", "fixtures.json"), ("hot", "hot_fixtures.json")):
            loaded = load_bench(spec)
            raw = json.loads((ROOT / "agit" / filename).read_text(encoding="utf-8"))
            self.assertEqual(
                [criterion.criterion_id for criterion in loaded.criteria],
                [item["id"] for item in raw["rubric"]],
            )
            self.assertEqual([criterion.text for criterion in loaded.criteria], [item["text"] for item in raw["rubric"]])
            self.assertEqual([case.case_id for case in loaded.cases], [item["id"] for item in raw["cases"]])
            self.assertEqual([case.task for case in loaded.cases], [item["task"] for item in raw["cases"]])
            self.assertEqual(
                [case.checks for case in loaded.cases],
                [{key: tuple(value) for key, value in item["checks"].items()} for item in raw["cases"]],
            )
        self.assertEqual(load_bench(), load_bench("fixture"))
        self.assertEqual(len(load_bench("hot").cases), 2)
        self.assertNotEqual(load_bench("hot").max_points(), load_bench().max_points())

        original = os.getcwd()
        self.addCleanup(os.chdir, original)
        os.chdir(self.temporary.name)
        Path("fixture").write_text("{not a bench}", encoding="utf-8")
        Path("hot").write_text("{not a bench}", encoding="utf-8")
        self.assertEqual(load_bench("fixture"), load_bench())
        self.assertEqual(len(load_bench("hot").cases), 2)

        with self.assertRaises(AgitError) as unknown:
            load_bench("no-such-bench")
        self.assertEqual(unknown.exception.status_code, 400)
        self.assertIn("unknown bench", unknown.exception.message)

        broken = Path(self.temporary.name) / "broken.json"
        broken.write_text("{", encoding="utf-8")
        with self.assertRaises(AgitError) as bad_json:
            load_bench(str(broken))
        self.assertEqual(bad_json.exception.status_code, 400)

        wide = Path(self.temporary.name) / "wide.json"
        wide.write_text(
            json.dumps(
                {
                    "rubric": [{"id": "outcome", "max_points": 5, "text": "Ask for a diff."}],
                    "cases": [{"id": "one", "task": "Show the diff.", "checks": {"outcome": ["diff"]}}],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(AgitError) as bad_points:
            load_bench(str(wide))
        self.assertIn("max_points", bad_points.exception.message)

        custom_path = Path(self.temporary.name) / "custom.json"
        custom_path.write_text(json.dumps(custom_bench_payload()), encoding="utf-8")
        custom = load_bench(str(custom_path))
        matched = StubJudge().score_harness(Harness(skill="show the diff", prompt="please"), custom)
        missed = StubJudge().score_harness(Harness(skill="hello", prompt="world"), custom)
        self.assertEqual(matched.points, 2)
        self.assertEqual(matched.max_points, 2)
        self.assertEqual(missed.points, 0)

    def test_gate_uses_the_selected_bench_score(self) -> None:
        created = create_project(self.store, "support-agent", GIT_REMOTE_URL)
        project_id = str(created["project_id"])
        api_key = str(created["api_key"])
        black = push_version(self.store, project_id, api_key, "hello", "world", "black")
        bootstrap_black(self.store, project_id, api_key, str(black["version_id"]))
        red = push_version(self.store, project_id, api_key, "show the diff", "please", "red")
        mark_red(self.store, project_id, api_key, str(red["version_id"]))
        hot = load_bench("hot")
        compared = compare_red_black(self.store, project_id, api_key, FixedJudge(), "hot")
        self.assertEqual(compared["judge_name"], "fixed")
        self.assertEqual(compared["max_points"], hot.max_points())
        self.assertNotEqual(compared["max_points"], load_bench().max_points())

        custom_path = Path(self.temporary.name) / "custom.json"
        custom_path.write_text(json.dumps(custom_bench_payload()), encoding="utf-8")
        gated = run_gate(self.store, project_id, api_key, 0.0, StubJudge(), str(custom_path))
        self.assertTrue(gated["promoted"])
        self.assertEqual(gated["max_points"], 2)
        self.assertEqual(gated["red_points"], 2)
        self.assertEqual(gated["black_points"], 0)
        self.assertEqual(gated["black_version_id"], red["version_id"])
        self.assertIsNone(gated["red_version_id"])

    def test_cli_and_http_pass_bench(self) -> None:
        created = self.run_agit("project", "create", "--name", "demo", "--git-url", GIT_REMOTE_URL)
        project_id = str(created["project_id"])
        api_key = str(created["api_key"])
        auth = ["--project", project_id, "--key", api_key]
        skill_path = Path(self.temporary.name) / "skill.txt"
        prompt_path = Path(self.temporary.name) / "prompt.txt"
        skill_path.write_text("aa", encoding="utf-8")
        prompt_path.write_text("b", encoding="utf-8")
        black = self.run_agit(
            "version",
            "push",
            *auth,
            "--skill-file",
            str(skill_path),
            "--prompt-file",
            str(prompt_path),
            "--message",
            "black",
        )
        self.run_agit("release", "black", *auth, "--version", str(black["version_id"]))
        skill_path.write_text("diff", encoding="utf-8")
        prompt_path.write_text("y", encoding="utf-8")
        red = self.run_agit(
            "version",
            "push",
            *auth,
            "--skill-file",
            str(skill_path),
            "--prompt-file",
            str(prompt_path),
            "--message",
            "red",
        )
        self.run_agit("release", "red", *auth, "--version", str(red["version_id"]))
        hot_max = load_bench("hot").max_points()
        compared = self.run_agit("compare", *auth, "--judge", "fixed", "--bench", "hot")
        self.assertEqual(compared["max_points"], hot_max)
        self.assertNotEqual(compared["max_points"], 18)

        unknown = subprocess.run(
            [
                sys.executable,
                "-m",
                "agit",
                "--store",
                str(self.store.path),
                "compare",
                *auth,
                "--judge",
                "stub",
                "--bench",
                "no-such-bench",
            ],
            cwd=ROOT,
            env={key: value for key, value in os.environ.items() if key != "AGIT_JUDGE_API_KEY"},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(unknown.returncode, 1)
        self.assertIn("unknown bench", unknown.stderr)

        custom_path = Path(self.temporary.name) / "custom.json"
        custom_path.write_text(json.dumps(custom_bench_payload()), encoding="utf-8")
        with running(build_server("127.0.0.1", 0, self.store.path)) as server:
            port = server.server_address[1]
            base = f"http://127.0.0.1:{port}"
            hot_status, hot_body = call(
                "POST",
                f"{base}/v1/projects/{project_id}/compare",
                {"judge": "fixed", "bench": "hot"},
                api_key=api_key,
            )
            self.assertEqual(hot_status, 200)
            self.assertEqual(hot_body["max_points"], hot_max)
            missing_status, missing = call(
                "POST",
                f"{base}/v1/projects/{project_id}/compare",
                {"bench": "no-such-bench"},
                api_key=api_key,
            )
            self.assertEqual(missing_status, 400)
            self.assertIn("unknown bench", str(missing["error"]))
            typed_status, typed = call(
                "POST",
                f"{base}/v1/projects/{project_id}/gate",
                {"bench": 1, "judge": "stub"},
                api_key=api_key,
            )
            self.assertEqual(typed_status, 400)
            self.assertIn("bench", str(typed["error"]))
            held_status, held = call(
                "POST",
                f"{base}/v1/projects/{project_id}/gate",
                {"margin": 1.1, "judge": "fixed", "bench": "hot"},
                api_key=api_key,
            )
            self.assertEqual(held_status, 200)
            self.assertFalse(held["promoted"])
            self.assertEqual(held["max_points"], hot_max)
            self.assertEqual(held["red_version_id"], red["version_id"])
        gated = self.run_agit("gate", *auth, "--judge", "stub", "--bench", str(custom_path), "--margin", "0")
        self.assertTrue(gated["promoted"])
        self.assertEqual(gated["max_points"], 2)
        self.assertEqual(gated["red_points"], 2)
        self.assertEqual(gated["black_points"], 0)
        self.assertEqual(gated["black_version_id"], red["version_id"])

    def run_agit(self, *args: str) -> dict[str, object]:
        completed = subprocess.run(
            [sys.executable, "-m", "agit", "--store", str(self.store.path), *args],
            cwd=ROOT,
            env={key: value for key, value in os.environ.items() if key != "AGIT_JUDGE_API_KEY"},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        parsed = json.loads(completed.stdout)
        self.assertIsInstance(parsed, dict)
        return parsed


def custom_bench_payload() -> dict[str, object]:
    return {
        "rubric": [
            {"id": "outcome", "max_points": 2, "text": "The harness asks for a diff."},
        ],
        "cases": [
            {
                "id": "ship_diff",
                "task": "Change the label and show the diff.",
                "checks": {"outcome": ["diff"]},
            },
        ],
    }


@contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def fake_judge_server() -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if body["model"] != "fake-model":
                raise AssertionError(body["model"])
            content = json.dumps(
                {
                    "scores": {"instruction_following": 2, "boundary": 2, "concreteness": 1},
                    "notes": "partial concreteness",
                }
            )
            fenced = "```json\n" + content + "\n```"
            payload = json.dumps({"choices": [{"message": {"content": fenced}}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    class Server(HTTPServer):
        allow_reuse_address = True

    return Server(("127.0.0.1", 0), Handler)


def call(
    method: str,
    url: str,
    payload: dict[str, object] | None = None,
    api_key: str | None = None,
) -> tuple[int, dict[str, object]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    http_request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        http_request.add_header("Content-Type", "application/json")
    if api_key is not None:
        http_request.add_header("Authorization", "Bearer " + api_key)
    try:
        with urllib.request.urlopen(http_request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as http_error:
        with http_error:
            return http_error.code, json.loads(http_error.read().decode("utf-8"))
