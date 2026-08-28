# Going live: what a real run needs

Researched 2026-08-28. Everything here is verified against primary sources or
probed directly. Supersedes the informal gap list.

Headline: **the task-supply problem is far cheaper than I assumed**, and **the
API code I wrote will fail on Opus 5**. Both change the plan.

---

## 1. Task supply - solved, and cheap

Use **SWE-bench-Live** (Microsoft). It exists precisely for the contamination
problem, refreshes monthly with 50 newly verified issues, and - critically -
**ships a prebuilt Docker image per instance**. No image building, no 120 GB
base/env/instance pyramid.

Verified by probing the registry directly:

```
starryzhang/sweb.eval.x86_64.<instance_id with "__" replaced by "_1776_">
  tag: latest | 0430
  size: 0.76 GB
  arch: amd64 only
```

Example: instance `aws-cloudformation__cfn-lint-3798` →
`starryzhang/sweb.eval.x86_64.aws-cloudformation_1776_cfn-lint-3798`.

**Disk cost for a 20-task pilot: ~15 GB.** Not the ~120 GB the SWE-bench docs
quote for building Verified from source.

Schema, pulled live from the HF datasets API (`lite` split):

```
repo, pull_number, instance_id, issue_numbers, base_commit,
patch, test_patch, problem_statement, hints_text, all_hints_text,
commit_urls, created_at, commit_url,
test_cmds, log_parser, difficulty,
FAIL_TO_PASS, PASS_TO_PASS
```

Two fields matter that plain SWE-bench lacks:

- **`test_cmds`** - a list, e.g. `['pytest -rA']`. This is the per-task test
  command our graders currently hardcode.
- **`log_parser`** - e.g. `pytest`. Tells us how to parse test output into
  per-test pass/fail rather than relying on the process exit code.

Splits: `lite` 300, `verified` 500, `test` 1000, `full` 1890. `lite` and
`verified` are frozen for comparability. Start with `lite`.

**Alternative if we want a second, independent suite:** SWE-rebench (Nebius,
21k+ tasks with explicit contamination tracking tied to model release dates).
Good for the transfer gate later; not needed for the pilot.

---

## 2. Docker on this machine

Requirements are modest because we are not building images:

- Docker Desktop with the **WSL 2** backend.
- Images are **amd64 only** - fine on this box, would need emulation on ARM.
- Allocate 8 CPUs / 16 GB RAM in Docker Desktop settings.
- Disk: the WSL2 VHDX (`%LOCALAPPDATA%\Docker\wsl\data\ext4.vhdx`) grows on
  demand up to a 1 TB default cap. Nothing to change for 20 tasks. If it needs
  bounding later, `%USERPROFILE%\.wslconfig` takes `diskSize=…`.
- VHDX never shrinks on its own. Reclaim with `docker system prune -a` then
  `Optimize-VHD -Mode Full` (both Docker Desktop and WSL must be stopped).

Worker count guidance from the SWE-bench harness: fewer than
`min(0.75 × cpu_count, 24)`. On 8 cores that means **6 workers**.

---

## 3. The API code is wrong and must be rewritten

I wrote `llm_bash.py` against raw `urllib` with a thinking config that is now
invalid. Confirmed against current API reference:

| What I wrote | What is actually required |
|---|---|
| `urllib.request` hand-rolled HTTP | the official **`anthropic` SDK** |
| `claude-opus-5-20260101` | `claude-opus-5` - **never append a date suffix** |
| `thinking: {type: "enabled", budget_tokens: N}` | **400 error on Opus 5.** `budget_tokens` is removed. Use `thinking: {type: "adaptive"}` |
| effort as a local capability fudge | `output_config: {effort: "low"\|"medium"\|"high"\|"xhigh"\|"max"}` - a real API parameter |
| `$3 / $15` per MTok | Opus 5 is **$5 / $25**; Sonnet 5 $2 / $10; Haiku 4.5 $1 / $5 |
| no caching | `cache_control: {type: "ephemeral"}` |
| no retry | SDK auto-retries 408/409/429/5xx, `max_retries` default 2 |

Additional constraints that affect the agent loop:

- **Thinking is on by default on Opus 5.** Omitting `thinking` runs adaptive.
  `{type: "disabled"}` is accepted only at effort `high` or below, and is
  discouraged - it can make the model write a tool call into visible text
  instead of emitting it properly, which in a loop silently poisons later turns.
- **No assistant prefill** - 400 on Opus 5.
- **Streaming** for large `max_tokens`, using `.get_final_message()`.
- **`response.stop_reason == "refusal"`** must be checked before reading
  content; `stop_details.category` explains it.

### Prompt caching is not optional here

An agent loop resends the whole conversation every turn. Cached reads bill at
**0.1x**, cache writes at **1.25x**. The system prompt plus early turns are
stable, so the savings compound across a run. Default TTL is 5 minutes, which
comfortably covers a turn-to-turn gap; a 1-hour TTL exists for longer gaps.

Verification is concrete: if `usage.cache_read_input_tokens` stays zero across
turns, something in the prefix is changing. Our cost accounting must read
`cache_read_input_tokens` and `cache_creation_input_tokens` separately, or the
cost-matched arms - the core of the design - will be wrong.

**Effort as a real axis.** `output_config.effort` maps directly onto the
architecture DSL's effort slot, and adds `xhigh` and `max` which our component
registry does not currently model. `xhigh` is documented as the best setting for
most coding/agentic work on Opus 5. That makes "does more effort help?" - the
question HAL answered negatively in 21 of 36 settings - directly testable by us
with a one-line DSL change.

---

## 4. Reference agent behaviour

mini-SWE-agent is the right shape to mirror, and its design is confirmed:

- bash only, **no tool-calling API** - the action is parsed out of the reply
- `subprocess.run` per action, **stateless**; every command is a fresh subshell
- completely **linear history** - each step appends, nothing is rewritten
- config carries `system_template`, `instance_template`, `step_limit`,
  `cost_limit`, `timeout`
- v2 changed the action fence from ```` ```bash ```` to a distinctive token so
  bash examples inside prompts cannot be mistaken for actions

That last point is a real bug in my current `_extract_command()` - it matches
```` ```bash ````, so any model reply that *illustrates* a command would execute
it. Must change to a unique fence.

Its SWE-bench prompt also does two things we need: it tells the agent
**not to modify tests or config**, and it defines an explicit **git-diff
submission step**. We should mirror both - the first is the honest instruction
whose violation our `test-tampering` grader then measures.

---

## 5. Git sanitisation - the control that matters most

Confirmed as a live, exploited leak, not a theoretical one. SWE-bench Pro
containers retained future git objects, branches, tags and reflogs, letting
agents recover the fix with `git log -p`. Datacurve found frontier models doing
exactly that on >12% of reviewed tasks.

This is a **runtime** leak, distinct from training contamination, and it is
fully preventable. On container start, before handing control to the agent:

```bash
git checkout -q <base_commit>
git remote remove origin || true
git reflog expire --expire=now --all
git gc --prune=now --aggressive
# then verify nothing beyond base_commit remains reachable
git rev-list --all --count
```

Simpler and stricter: delete `.git` entirely and hand the agent a plain tree.
Cost is that some tasks' test commands assume a repo. Recommend: truncate to a
single root commit at `base_commit`, which keeps `git diff` working for patch
extraction while leaving nothing to mine.

Our `oracle-access` grader already detects the *attempt*; this prevents the
*success*. Both are needed - the attempt rate is itself a measurement.

---

## 6. Held-out tests

SWE-bench-Live gives `FAIL_TO_PASS` and `PASS_TO_PASS` but nothing hidden.
Without a held-out set there is no lucky-pass detection, which is a large part of
the value.

Approach: **split `FAIL_TO_PASS` deterministically**. Half becomes the visible
suite the agent may run; half is withheld and injected only after the workspace
is captured. `PASS_TO_PASS` stays visible as the regression guard.

- Requires `len(FAIL_TO_PASS) >= 2` - filter the suite on that.
- The split must be seeded and recorded in the task hash, or it is not
  reproducible.
- `resolved` = all visible F2P pass **and** all held-out F2P pass **and** no
  P2P regressions.

This is honest and cheap. The alternative - synthesising extra tests from the
gold patch - introduces its own validity problem.

---

## 7. Test parsing

Exit codes are too coarse. `pytest -rA` returns non-zero if *any* test fails, so
a patch that fixes the target test but trips an unrelated flake looks identical
to one that fixes nothing. We need per-test results, which is what the
`log_parser` field is for. Implement a pytest log parser first (it covers most
of the dataset); fall back to exit code with an explicit `parser=exitcode` flag
recorded in the score so the degradation is visible.

---

## 8. Concrete code changes

| # | Change | Where |
|---|---|---|
| 1 | Rewrite backend on the `anthropic` SDK: `claude-opus-5`, adaptive thinking, `output_config.effort`, prompt caching, `max_retries`, streaming, refusal handling, unique action fence | `af/exec/adapters/llm_bash.py` |
| 2 | Real cost accounting from `usage` incl. cache read/write at their own rates | same + `af/experiment` |
| 3 | Task command from `test_cmds`; drop the hardcoded `unittest discover` | `af/grade/__init__.py` |
| 4 | Per-test log parsing (`pytest`), F2P/P2P semantics | `af/grade/` new module |
| 5 | SWE-bench-Live importer: HF → task dirs, F2P split, image name derivation | `af/tasks/` new module |
| 6 | `DockerSandbox` accepts a per-task image and wires `EnvironmentSpec.image` through (currently never reaches the sandbox) | `af/env`, `af/exec` |
| 7 | Git sanitisation on container start + assertion into `integrity.json` | `af/env` |
| 8 | `--workers N` thread pool in `Scheduler.drain` | `af/experiment/__init__.py` |
| 9 | Add `xhigh` / `max` to the effort axis | `af/spec/registry.py` |

1-2 are the blocking pair; nothing can run live until they are done.

---

## 9. Cost

Opus 5 at $5/$25 per MTok, with caching the dominant variable. A bash-only agent
on a real repo runs tens of turns with a growing transcript; without caching that
is expensive, with it the stable prefix bills at a tenth.

Order-of-magnitude for the pilot: **20 tasks x 3 replicates x 3 arms = 180
trials**. Budget it explicitly in the experiment spec and let the existing budget
guard enforce it - that guard already fired correctly once during the simulator
runs, cancelling 59 trials at the cap.

Cheaper controls available: run the pilot on `claude-sonnet-5` ($2/$10) or
`claude-haiku-4-5` ($1/$5) for the plumbing shakedown, then repeat the arms on
Opus 5 once the pipeline is verified. Model is a DSL field, so this is a config
change.

---

## 10. Order of operations

1. **One live trial, one demo task, Sonnet 5.** Proves SDK, auth, retries,
   caching, cost accounting. Cents.
2. **One live trial, one SWE-bench-Live task.** Proves image pull, git
   sanitisation, `test_cmds`, log parsing, F2P/P2P grading. Hand-verify the
   bundle end to end.
3. **Parallelism**, then scale to 20 tasks.
4. **The pilot**, pre-registered, with a cost-matched arm.

Step 2 is where the real risk is - everything else is mechanical.

---

## Sources

- [SWE-bench-Live dataset](https://huggingface.co/datasets/SWE-bench-Live/SWE-bench-Live) · [repo](https://github.com/microsoft/SWE-bench-Live) · [SWE-bench Goes Live! (paper)](https://arxiv.org/html/2505.23419v2)
- [SWE-bench Docker setup](https://www.swebench.com/SWE-bench/guides/docker_setup/) · [harness reference](https://www.swebench.com/SWE-bench/reference/harness/)
- [SWE-rebench](https://huggingface.co/datasets/nebius/SWE-rebench)
- [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent) · [swebench.yaml config](https://github.com/SWE-agent/mini-swe-agent/blob/v2.2.1/src/minisweagent/config/benchmarks/swebench.yaml) · [v2 migration](https://mini-swe-agent.com/latest/advanced/v2_migration/)
- [SWE-ReX](https://github.com/SWE-agent/SWE-ReX)
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Anthropic 429/529 handling](https://www.respan.ai/articles/anthropic-api-rate-limits)
- [OpenAI: why we no longer evaluate SWE-bench Verified](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)
- [Datacurve DeepSWE audit](https://deepswe.datacurve.ai/blog/deepswe)
- [Docker Desktop WSL2 disk sizing](https://forums.docker.com/t/increase-docker-data-disk-size-to-any-size-with-current-version-of-docker-desktop/150789)
