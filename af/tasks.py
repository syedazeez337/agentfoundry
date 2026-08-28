"""Real-repo task import, without containers.

A task fixture is a repository cloned at a pinned commit with its history
truncated. Its environment is reconstructed at grade time by `uv` in a couple of
seconds, which is what a prebuilt image would otherwise have frozen.

Constraints of this approach, stated rather than hidden:
  - pure-Python repos only; C extensions and system libraries need a container
  - the tool chain must be pinnable (pytest version, etc.)
  - network isolation is best-effort outside a container
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from af.util import dump_yaml, write_atomic, write_json


@dataclass
class RepoTask:
    id: str
    repo: str                       # e.g. "pallets/click"
    commit: str
    instruction: str
    difficulty: float = 0.5
    suite: str = "repo"
    category: str = "bugfix"
    python: str = "3.12"
    pins: tuple = ("pytest==8.2.*",)
    extra_setup: tuple = ()
    test_cmd: str = "{python} -m pytest -q"
    held_out_cmd: str = ""
    breaking_patch: dict = field(default_factory=dict)   # {path: content}
    solution: dict = field(default_factory=dict)
    partial: dict = field(default_factory=dict)
    cheat: dict = field(default_factory=dict)

    @property
    def setup(self) -> list[str]:
        pins = " ".join(f'"{p}"' for p in self.pins)
        return [
            f"uv venv .venv --python {self.python}",
            f"uv pip install --python .venv -e . {pins}",
            *self.extra_setup,
        ]


def _git(argv: list[str], cwd: Path, timeout: int = 600) -> str:
    p = subprocess.run(["git", *argv], cwd=str(cwd), capture_output=True,
                       text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(argv)} failed: {p.stderr.strip()[:400]}")
    return p.stdout.strip()


def clone_at_commit(repo: str, commit: str, dest: Path,
                    truncate_history: bool = True) -> str:
    """Clone, pin, and sanitise.

    History truncation is the control that matters: Datacurve found frontier
    models recovering gold patches from shipped `.git` history. We remove the
    thing rather than only detecting the attempt.
    """
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)

    url = repo if repo.startswith("http") else f"https://github.com/{repo}.git"
    tmp = Path(tempfile.mkdtemp(prefix="af-clone-"))
    try:
        _git(["clone", "--quiet", "--filter=blob:none", url, "work"], tmp)
        work = tmp / "work"
        resolved = commit
        if not commit or commit in ("HEAD", "latest"):
            resolved = _git(["rev-parse", "HEAD"], work)
        _git(["checkout", "--quiet", "--detach", resolved], work)
        resolved = _git(["rev-parse", "HEAD"], work)

        if truncate_history:
            # Collapse to a single root commit at the pinned tree: `git diff`
            # still works for patch extraction, but there is no future history
            # to mine and no remote to fetch from.
            shutil.rmtree(work / ".git", ignore_errors=True)
            _git(["init", "--quiet", "-b", "main"], work)
            _git(["-c", "user.email=af@local", "-c", "user.name=AgentFoundry",
                  "add", "-A"], work)
            _git(["-c", "user.email=af@local", "-c", "user.name=AgentFoundry",
                  "commit", "--quiet", "-m", f"fixture at {resolved[:12]}"], work)

        shutil.copytree(work, dest, dirs_exist_ok=True)
        return resolved
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build_task(root: Path, spec: RepoTask, truncate_history: bool = True) -> Path:
    """Materialise a task directory the rest of AgentFoundry already understands."""
    d = Path(root) / "tasks" / spec.suite / spec.id
    fixture = d / "fixture"
    resolved = clone_at_commit(spec.repo, spec.commit, fixture, truncate_history)

    # Introduce the defect. A real import would instead check out the parent of
    # a fix commit; this path supports hand-authored regressions too.
    for rel, content in (spec.breaking_patch or {}).items():
        p = fixture / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    write_json(d / "solution.json", {"files": spec.solution,
                                     "partial_files": spec.partial or spec.solution})
    write_json(d / "cheat.json", {"files": spec.cheat})

    write_atomic(d / "task.yaml", dump_yaml({
        "id": spec.id,
        "suite": spec.suite,
        "category": spec.category,
        "difficulty": spec.difficulty,
        "instruction": spec.instruction,
        "repo": spec.repo,
        "commit": resolved,
        "setup": spec.setup,
        "test_cmd": spec.test_cmd,
        "held_out_cmd": spec.held_out_cmd or spec.test_cmd,
        "test_parser": "pytest",
        "env": {"image": "uv:local", "net": "deny"},
    }))
    return d


# ------------------------------------------------- mutation-based synthesis
# Take a real repository, break one real function, and let the repository's own
# test suite be the grader. The solution is the original source, so correctness
# is not a judgement call. This is how SWE-smith scales task creation, and it
# works without containers because the repo is the fixture.

MUTATIONS: dict[str, tuple[str, str]] = {
    # name: (regex, replacement) applied once to the target function body
    "invert-condition": (r"\bif (not )?(\w+[\w\.\(\)\[\]']*):",
                         lambda m: f"if {'' if m.group(1) else 'not '}{m.group(2)}:"),
    "off-by-one": (r"\brange\(([^,)]+), ([^,)]+)\)", r"range(\1, \2 - 1)"),
    "drop-return": (r"\n(\s+)return (.+)", r"\n\1return None  # \2"),
    "swap-comparison": (r"([\w\.]+) (<=|>=|<|>) ([\w\.]+)",
                        lambda m: f"{m.group(1)} {{'<':'>','>':'<','<=':'>=','>=':'<='}}['{m.group(2)}'] {m.group(3)}"),
}


def extract_function(source: str, func: str) -> tuple[int, int] | None:
    """Byte span of a top-level `def func(...)` including its body."""
    import re

    m = re.search(rf"^def {re.escape(func)}\b", source, re.M)
    if not m:
        m = re.search(rf"^(\s+)def {re.escape(func)}\b", source, re.M)
        if not m:
            return None
    start = m.start()
    indent = len(source[start:].split("def")[0])
    lines = source[start:].splitlines(keepends=True)
    end = start + len(lines[0])
    for line in lines[1:]:
        stripped = line.lstrip()
        if stripped and (len(line) - len(stripped)) <= indent and not stripped.startswith(("#", ")")):
            break
        end += len(line)
    return start, end


def mutate_function(source: str, func: str, kind: str = "drop-return") -> str | None:
    """Introduce one defect into one function. Returns None if it did not apply."""
    import re

    span = extract_function(source, func)
    if span is None:
        return None
    start, end = span
    body = source[start:end]
    pattern, repl = MUTATIONS[kind]
    mutated, n = re.subn(pattern, repl, body, count=1)
    if n == 0 or mutated == body:
        return None
    return source[:start] + mutated + source[end:]


def synthesize_task(root: Path, repo: str, target_file: str, func: str,
                    test_cmd: str, held_out_cmd: str = "",
                    kind: str = "drop-return", commit: str = "HEAD",
                    pins: tuple = ("pytest==8.2.*",), python: str = "3.12",
                    suite: str = "repo", difficulty: float = 0.5,
                    instruction: str = "") -> dict:
    """Clone a repo, break one function, and emit an AgentFoundry task.

    The reference solution is the untouched original, so `af task validate` can
    prove admissibility mechanically: fixture fails, reference passes, null
    patch fails, cheat patch fails.
    """
    task_id = f"{repo.split('/')[-1]}-{func.replace('_', '-')}-{kind}"
    d = Path(root) / "tasks" / suite / task_id
    fixture = d / "fixture"
    resolved = clone_at_commit(repo, commit, fixture)

    target = fixture / target_file
    if not target.exists():
        raise FileNotFoundError(f"{target_file} not present in {repo}")
    original = target.read_text(encoding="utf-8")

    broken = mutate_function(original, func, kind)
    if broken is None:
        raise RuntimeError(
            f"mutation {kind!r} did not apply to {func}() in {target_file}")
    target.write_text(broken, encoding="utf-8")

    spec = RepoTask(
        id=task_id, repo=repo, commit=resolved, suite=suite,
        difficulty=difficulty, python=python, pins=pins,
        test_cmd=test_cmd, held_out_cmd=held_out_cmd or test_cmd,
        instruction=instruction or (
            f"A defect was introduced in `{func}()` in `{target_file}`. "
            f"The repository's test suite is failing as a result. "
            f"Find and fix it. Do not modify tests."),
        solution={target_file: original},
        partial={target_file: original},
        cheat={},
    )

    write_json(d / "solution.json", {"files": spec.solution,
                                     "partial_files": spec.partial})
    write_json(d / "cheat.json", {"files": _cheat_for(test_cmd)})
    write_atomic(d / "task.yaml", dump_yaml({
        "id": spec.id, "suite": spec.suite, "category": "bugfix",
        "difficulty": spec.difficulty, "instruction": spec.instruction,
        "repo": spec.repo, "commit": spec.commit,
        "setup": spec.setup,
        "test_cmd": spec.test_cmd,
        "held_out_cmd": spec.held_out_cmd,
        "test_parser": "pytest",
        "env": {"image": "uv:local", "net": "deny"},
    }))
    return {"id": task_id, "dir": str(d), "commit": resolved,
            "mutation": kind, "target": f"{target_file}::{func}"}


def _cheat_for(test_cmd: str) -> dict:
    """A conftest that makes every test pass. The grader must reject it."""
    return {
        "conftest.py": (
            "import pytest\n\n\n"
            "def pytest_collection_modifyitems(items):\n"
            "    for item in items:\n"
            "        item.obj = lambda *a, **k: None\n"
        )
    }
