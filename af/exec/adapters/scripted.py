"""Scripted backend: a deterministic agent simulator.

Its job is to exercise every layer above L2 end to end with zero API keys and
zero dollars, while producing signal that is *structurally* realistic:

  - success depends on architecture capability vs task difficulty
  - some passes are "lucky" (visible tests pass, held-out fail)
  - some runs attempt oracle access (git history) or test tampering
  - cost and turn counts track the architecture's derived multipliers

It is not a claim about the world. It is a fixture that makes the pipeline
testable. Real conclusions come from the llm-bash backend or a vendor adapter.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from af.exec import RunOutcome, TrialContext, register_backend
from af.util import seeded_rng


def _logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class ScriptedBackend:
    name = "scripted"
    version = "1.0.0"

    def capabilities(self) -> set[str]:
        return {"*"}

    # ---------------------------------------------------------- provision

    def provision(self, ctx: TrialContext) -> None:
        ctx.sink.emit("agent.start", agent_id="root", role="implement",
                      model=ctx.arch.models.get("default", {}).get("id", "scripted"))

    # ------------------------------------------------------------- invoke

    def invoke(self, ctx: TrialContext) -> RunOutcome:
        rng = seeded_rng(ctx.trial.trial_key)
        arch, task, sink = ctx.arch, ctx.task, ctx.sink

        solution = _load(task.solution_path)
        cheat = _load(task.cheat_path)

        cap = arch.capability
        difficulty = float(task.difficulty)
        stages = arch.stages or ["implement"]
        has_verification = arch.slots.get("verification", {}).get("component") != "none"
        has_research = "research" in stages
        has_review = "review" in stages

        p_solve = _logistic(6.0 * (cap - difficulty))
        p_lucky = 0.18 if not has_verification else 0.06
        p_oracle = 0.10 if not has_verification else 0.03
        p_tamper = 0.09 if not has_verification else 0.02

        base_turns = max(4, int(8 * arch.turn_mult * (0.6 + difficulty)))
        turns = min(int(ctx.trial.budget.get("max_turns", 60)), base_turns)

        tokens = 0
        oracle_attempts: list[str] = []
        git_ops: list[str] = []

        # --- exploration
        n_explore = 3 + (4 if has_research else 0) + int(rng.random() * 3)
        for _ in range(n_explore):
            tokens += self._model_turn(ctx, rng, "explore", 1400, arch)
            cmd = rng.choice(["ls -R", "grep -rn def src", "cat src/mod.py",
                              "sed -n 1,80p src/mod.py"])
            sink.emit("tool.call", **{"gen_ai.tool.name": "bash", "command": cmd})
            r = ctx.sandbox.exec(ctx.handle, _argv(cmd), timeout=30)
            sink.emit("tool.result", exit_code=r.returncode,
                      bytes=len(r.stdout), phase="explore")

        # --- oracle access attempt (integrity graders must catch this)
        if rng.random() < p_oracle:
            cmd = "git log --all -p"
            sink.emit("tool.call", **{"gen_ai.tool.name": "bash", "command": cmd})
            r = ctx.sandbox.exec(ctx.handle, _argv(cmd), timeout=15)
            sink.emit("tool.result", exit_code=r.returncode, phase="explore",
                      note="history probe")
            oracle_attempts.append(cmd)
            git_ops.append(cmd)

        # --- implementation
        solved = rng.random() < p_solve
        lucky = (not solved) and rng.random() < p_lucky
        tampered = (not solved and not lucky) and rng.random() < p_tamper

        attempts = 1
        max_attempts = 3 if has_review else 2
        while attempts <= max_attempts:
            tokens += self._model_turn(ctx, rng, "implement", 2600, arch)

            if solved:
                files = solution.get("files", {})
            elif lucky:
                files = solution.get("partial_files", solution.get("files", {}))
            elif tampered:
                files = cheat.get("files", {})
            else:
                files = _degrade(solution.get("files", {}), rng)

            for rel, content in files.items():
                p = ctx.handle.workdir / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
                sink.emit("file.write", path=rel, bytes=len(content), phase="implement")

            if not has_verification:
                break

            cmd = arch.slots["verification"]["params"].get(
                "command", "python -m unittest discover -s tests")
            sink.emit("tool.call", **{"gen_ai.tool.name": "bash", "command": cmd},
                      phase="verify")
            r = ctx.sandbox.exec(ctx.handle, _argv(cmd), timeout=120)
            sink.emit("tool.result", exit_code=r.returncode, phase="verify",
                      passed=r.returncode == 0)
            if r.returncode == 0:
                break

            # a repair round only helps if the architecture has review capacity
            if has_review and not solved and rng.random() < 0.45:
                solved = True
                lucky = False
                tampered = False
                sink.emit("message.send", agent_id="review", parent_agent_id="root",
                          content="found defect, returning to implement")
            attempts += 1

        if has_review:
            tokens += self._model_turn(ctx, rng, "review", 1800, arch)
            sink.emit("agent.end", agent_id="review")

        # --- context management is visible in the stream when configured
        if arch.slots.get("context", {}).get("component") in (
            "compact-on-threshold", "rolling-summary", "progressive-disclosure"
        ):
            sink.emit("context.compact", before_tokens=tokens,
                      after_tokens=int(tokens * 0.6))

        if arch.slots.get("memory", {}).get("component") != "none":
            sink.emit("memory.write", items=int(2 + rng.random() * 4))

        sink.emit("agent.end", agent_id="root")

        price = 3.0 / 1_000_000  # nominal $/token for the simulator
        cost = tokens * price * arch.cost_mult
        return RunOutcome(
            stopped_reason="finished",
            usage={
                "gen_ai.usage.input_tokens": int(tokens * 0.8),
                "gen_ai.usage.output_tokens": int(tokens * 0.2),
                "total_tokens": tokens,
                "cost_usd": round(cost, 4),
                "turns": turns,
                "model_calls": max(1, sink.count_by_type.get("model.request", 0)),
            },
            raw={
                "simulator": {
                    "p_solve": round(p_solve, 4),
                    "capability": round(cap, 4),
                    "difficulty": difficulty,
                    "intended": "solved" if solved else "lucky" if lucky
                                else "tampered" if tampered else "failed",
                },
                "integrity": {"oracle_attempts": oracle_attempts, "git_ops": git_ops},
            },
        )

    def _model_turn(self, ctx: TrialContext, rng, phase: str, base: int, arch) -> int:
        n = int(base * (0.7 + rng.random() * 0.6) * arch.cost_mult)
        ctx.sink.emit("model.request", phase=phase,
                      **{"gen_ai.request.model": arch.models.get("default", {}).get("id", "scripted")})
        ctx.sink.emit("model.response", phase=phase,
                      **{"gen_ai.usage.input_tokens": int(n * 0.8),
                         "gen_ai.usage.output_tokens": int(n * 0.2)})
        return n

    # ------------------------------------------------------ collect/normalize

    def collect(self, ctx: TrialContext) -> dict:
        # Integrity is reported through invoke()'s RunOutcome.raw, where it is
        # actually observed. Returning empty lists here claimed to be the
        # channel and was not.
        return {"backend": self.name, "version": self.version}

    def normalize(self, raw: dict, sink) -> None:
        return None


def _argv(cmd: str) -> list[str]:
    """Map the simulator's shell strings onto portable argv."""
    from af.env import PYTHON

    if cmd.startswith("python "):
        return [PYTHON, *cmd.split()[1:]]
    if cmd.startswith("git "):
        return ["git", *cmd.split()[1:]]
    if cmd.startswith("cat "):
        return [PYTHON, "-c",
                f"import pathlib,sys;p=pathlib.Path({cmd.split()[1]!r});"
                f"sys.stdout.write(p.read_text() if p.exists() else '')"]
    if cmd.startswith("ls"):
        return [PYTHON, "-c",
                "import pathlib;[print(p) for p in sorted(pathlib.Path('.').rglob('*'))]"]
    if cmd.startswith("grep") or cmd.startswith("sed"):
        return [PYTHON, "-c", "pass"]
    return [PYTHON, "-c", "pass"]


def _load(path: Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _degrade(files: dict[str, str], rng) -> dict[str, str]:
    """A plausible-but-wrong edit: the file is touched, the defect survives."""
    import re

    out = {}
    for rel, content in files.items():
        broken = re.sub(
            r"\n(\s+)return (.+)",
            lambda m: f"\n{m.group(1)}return None  # {m.group(2)}",
            content,
            count=1,
        )
        out[rel] = "# attempted fix\n" + broken
    return out


register_backend(ScriptedBackend())
