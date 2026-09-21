# my-agit

my-agit is version control for an agent harness, plus a red/black release gate.

A project is bound to a git remote URL. The URL is stored on the project; v0 does not clone it. Each version stores two text artifacts: a skill and a prompt. One version is **black** (live). Another may be **red** (candidate). The gate scores both on the same fixture benchmark and rubric, and promotes red to black only when the candidate leads live by the configured margin. Otherwise black stays.

v0 does not train weights, distill traces, shadow live traffic, or version tools, runtimes, fallbacks, MCP servers, or sandboxes.

## Roadmap

Development guide and phased plan: [docs/ROADMAP.md](docs/ROADMAP.md).

The judge calls an OpenAI-compatible chat completion. With no API key, a deterministic stub scores the same cases by matching fixture phrases, so the demo and CI run offline.

## Run the vertical slice

Python 3.11+, from the repo root:

```bash
python3 -m unittest discover -s tests -v
python3 -m agit demo
```

`agit demo` always uses the stub judge. It creates a project bound to `git@github.com:acme/support-agent.git`, marks a baseline skill and prompt black, rejects a weaker red candidate, then promotes a stronger one. It prints one JSON object.

The same path by hand, nine commands. Copy `project_id`, `api_key`, and `version_id` from each JSON response:

```bash
export AGIT_STORE="$PWD/.agit/store.json"
python3 -m agit project create --name demo --git-url git@github.com:acme/support-agent.git
export AGIT_PROJECT=proj_... AGIT_API_KEY=agk_...
python3 -m agit version push --skill-file examples/baseline_skill.md --prompt-file examples/baseline_prompt.md --message baseline
python3 -m agit release black --version <version_id>
python3 -m agit version push --skill-file examples/stronger_skill.md --prompt-file examples/stronger_prompt.md --message stronger
python3 -m agit release red --version <version_id>
python3 -m agit gate --judge stub --margin 0
python3 -m agit release list
```

`release black` only bootstraps the first live version. After that, black changes through `gate`. `compare` prints the same scores without changing the release.

## Auth and versions

Mutating commands and project reads take the project API key (`--key` or `AGIT_API_KEY`) and project id (`--project` or `AGIT_PROJECT`). Create prints the key once. The store keeps only its SHA-256.

The version id is the SHA-256 of the skill bytes, a NUL byte, and the prompt bytes. Pushing the same pair again returns the stored version.

The store file defaults to `.agit/store.json` (`--store` or `AGIT_STORE`).

## Gate

Three fixture cases, three criteria (`instruction_following`, `boundary`, `concreteness`), each 0, 1, or 2. A harness score is those points over the bench maximum (18 in the built-in bench). These are rubric points.

Promote when the candidate's points are strictly higher and the lead is at least `margin * max_points`. The default margin is `0`, so a tie stays black. A rejected candidate stays red.

One gate run scores both harnesses with the same judge. The stub looks for phrases listed under `checks` in `agit/fixtures.json`. The HTTP judge scores the rubric text and the case task, and can return different points.

## Judge environment

| Variable | Role |
| --- | --- |
| `AGIT_JUDGE_BASE_URL` | API root. Default `https://api.openai.com/v1`. The client posts to `{BASE_URL}/chat/completions`. |
| `AGIT_JUDGE_API_KEY` | Bearer token. Unset or blank selects the stub when `--judge auto`. |
| `AGIT_JUDGE_MODEL` | Model name. Default `gpt-4o-mini`. |

`--judge stub` forces the offline judge. `--judge auto` is the default.

## HTTP

```bash
python3 -m agit serve --host 127.0.0.1 --port 8787
```

| Method | Path | Auth |
| --- | --- | --- |
| POST | `/v1/projects` | no |
| GET | `/v1/projects/{id}` | bearer |
| POST | `/v1/projects/{id}/versions` | bearer |
| GET | `/v1/projects/{id}/versions` | bearer |
| GET | `/v1/projects/{id}/versions/{version_id}` | bearer |
| POST | `/v1/projects/{id}/black` | bearer |
| POST | `/v1/projects/{id}/red` | bearer |
| POST | `/v1/projects/{id}/compare` | bearer |
| POST | `/v1/projects/{id}/gate` | bearer |
| GET | `/v1/projects/{id}/releases` | bearer |

Create body: `{"name": "demo", "git_remote_url": "git@github.com:acme/support-agent.git"}`. The response includes `api_key`. Later requests send `Authorization: Bearer agk_...`.

Version body: `{"skill": "...", "prompt": "...", "message": "..."}`.

Gate body: `{"margin": 0, "judge": "stub"}`. Both fields are optional.

## Layout

- `agit/store.py` — JSON project store and release history
- `agit/core.py` — versions and the red/black gate
- `agit/bench.py` — fixture bench and rubric (`agit/fixtures.json`)
- `agit/judge.py` — offline stub and OpenAI-compatible judge
- `agit/api.py` — HTTP
- `agit/cli.py` — CLI (`python3 -m agit`)
- `examples/` — skill and prompt files used by the demo

## License

MIT.
