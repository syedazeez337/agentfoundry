# Do we actually need Docker?

Researched and tested 2026-08-28. Short answer: **no, not for AgentFoundry's own
task suite.** Docker is required only if we borrow someone else's prebuilt task
images.

---

## What the experiment showed

I took a real repository, pinned it to a 2024 commit, and set it up with `uv` -
no container anywhere:

```
git clone --filter=blob:none pallets/click   5.0 s
git checkout <commit from 2024-05-31>
uv venv + uv pip install -e . pytest         1.8 s
pytest                                       FAILED
```

The failure was not permissions, not missing system libraries, not architecture.
It was this:

```
pytest.PytestRemovedIn10Warning: Passing a non-Co...
Interrupted: 1 error during collection
```

Modern pytest (9.1.1) against a repo written for pytest 8.x. One pin fixed it:

```
uv pip install "pytest==8.2.*"               0.5 s
pytest -q  ->  588 passed, 27 skipped, 1 xfailed in 1.31 s
```

**Total: ~9 seconds, 16 MB, zero containers.**

That is the whole finding. The reason SWE-bench ships 760 MB images per instance
is not isolation - it is that reconstructing "the dependency graph as it was on
the day of that commit" across 500 heterogeneous repos is genuinely hard.
Freezing a filesystem is the brute-force solution. But the *actual* requirement
is a few lines of pinned metadata, and `uv` resolves it in under a second.

---

## The honest threat model

I over-stated the security case earlier. Being precise about who the adversary
is:

| Risk | Real? | Does Docker fix it? | Does process isolation fix it? |
|---|---|---|---|
| Agent runs `rm -rf` outside the workspace | Yes, plausibly by accident | Yes | Yes - scratch dir per trial, already implemented |
| Agent phones home / fetches the fix | **Yes, observed in the wild** | Yes | Partly - env-level proxy block; a determined process can bypass |
| Agent leaves a runaway process | Yes | Yes | Partly - timeouts, no cgroup |
| Agent escapes the sandbox to attack the host | Only if the *repo* or the *model* is hostile | Yes | No |
| Two trials contaminate each other | Yes | Yes | Yes - fresh copy per trial, already implemented |

The last row is the only one Docker uniquely solves, and it matters when running
**untrusted third-party repositories**. For a curated suite of well-known
open-source projects driven by Claude, the realistic failure is a mess, not an
escape.

That said, the network row is the one I would not wave away: oracle access is a
*measured* problem, not hypothetical. It needs a real control, and env-var
proxying is weak. See the mitigation below.

---

## The five options

| # | Approach | Docker? | Works today on Win/mac/Linux | Cost | Verdict |
|---|---|---|---|---|---|
| 1 | **uv-native tasks** - our own suite, pinned deps | No | Yes | Free | **Default.** Proven above. |
| 2 | **WSL2 direct** - real Linux, no container runtime | No | Windows only | Free | Good second stage; you already have WSL2 |
| 3 | **Cloud sandbox** (E2B / Modal / Daytona) | No | Yes | ~$0.05/vCPU-hr; E2B $100 free credit, Daytona $200 | Escape hatch for real isolation without local infra |
| 4 | **Podman** | Container runtime, not Docker | Yes | Free, no licence cliff | Trades Docker Desktop's licence for the same WSL2 setup pain |
| 5 | **Docker Desktop** | Yes | Yes | Free under 250 employees | Only needed for SWE-bench-Live's prebuilt images |

Note that SWE-bench itself offers cloud evaluation via **Modal** and `sb-cli`
(AWS) precisely so people can skip local Docker. We are not inventing an unusual
position.

---

## What we lose by not using SWE-bench-Live images

Being straight about the tradeoff:

- **Comparability.** Our numbers will not sit on a public leaderboard. Given
  that the leaderboard is contaminated and its graders mis-score roughly a third
  of trials, this is a smaller loss than it sounds - and AgentFoundry's thesis
  was never "beat SWE-bench," it was "measure architecture differences
  correctly."
- **Task volume.** We hand-curate instead of importing 1,890 rows. Mitigated by
  restricting to repos whose setup is `uv pip install -e .`, which is most
  pure-Python projects.
- **Repos with C extensions or system libraries** (numpy, scipy, lxml, Pillow)
  are hard without containers. We exclude them, and record that as a stated
  limitation of the suite rather than pretending otherwise.

What we gain: the suite runs on any machine in seconds, tasks are ours so we
control the four-check admissibility contract, and we can build held-out tests
properly instead of splitting someone else's `FAIL_TO_PASS`.

This is exactly what the original v0 spec called for in section 22 - "the point
isn't to beat SWE-bench; the point is a benchmark whose purpose is architecture
experimentation."

---

## Network control without containers

The one control that genuinely needs strengthening. Options, weakest first:

1. **Proxy env vars** (`HTTP_PROXY=http://127.0.0.1:9`) - current implementation.
   Blocks well-behaved libraries; a determined process ignores it.
2. **Loopback-only DNS / hosts override** in the child environment - stronger,
   still bypassable by raw IP.
3. **Detect rather than prevent** - our `oracle-access` grader already reads the
   event stream for network calls and git history probes, and flags the trial
   `invalid`. Detection is what the *measurement* actually needs.
4. **Truncate the git history** so there is nothing to find locally, which we do
   anyway.
5. **Real network namespace** - requires a container or WSL2.

Recommended stance: do 1, 2, 3 and 4 by default, and say plainly in the
integrity record that network denial is best-effort outside a container. When a
result depends on it, re-run those trials under option 3 (cloud sandbox) or 5.

---

## Recommendation

**Three sandbox backends behind the existing `Sandbox` protocol**, chosen by
config, no code changes above L1:

- `local` (default) - uv-native, works everywhere, what the demo already uses
- `wsl` - runs the same task tree inside WSL2 for Linux-native execution
- `remote` - E2B or Modal for trials that need a real boundary

Docker stays supported and stays optional. It becomes the answer to "I want
SWE-bench-Live's exact images," not a prerequisite for using the tool.

`af doctor` reflects this as of commit 44bb976: Docker and WSL moved to an
`optional` tier reporting INFO, and `uv` was added to the `real` tier as the
thing that actually replaces prebuilt images. With a provider key and no Docker
installed, `real_runs` reports READY. `tests/test_doctor.py` asserts it.
