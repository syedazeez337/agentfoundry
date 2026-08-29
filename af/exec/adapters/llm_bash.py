"""llm-bash: a real single-tool agent loop.

Deliberately minimal - one bash tool, no tool-calling ceremony, stateless
subprocess per action. This is the "few good tools" end of the action-interface
axis and doubles as the reference implementation for real adapters.

Uses only the standard library so the package has no provider SDK dependency.
Set ANTHROPIC_API_KEY (or OPENAI_API_KEY) to use it. Without a key it refuses
loudly rather than silently degrading.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from af.exec import RunOutcome, TrialContext, register_backend

SYSTEM = """You are a software engineer working in a repository.
You have exactly one tool: a shell. Emit exactly one command per reply inside
a fenced block:

```bash
<command>
```

Inspect the code, make the change, run the tests. When the task is complete and
tests pass, reply with exactly: TASK_COMPLETE
Do not read git history or look for reference solutions."""


class LlmBashBackend:
    name = "llm-bash"
    version = "1.0.0"

    def capabilities(self) -> set[str]:
        return {
            "action:bash-only",
            "context:full-history", "context:compact-on-threshold",
            "context:rolling-summary", "context:progressive-disclosure",
            "memory:none", "memory:task-local",
            "control:single", "control:staged",
            "verification:none", "verification:project-tests",
            "verification:tests-plus-review",
        }

    def provision(self, ctx: TrialContext) -> None:
        model = ctx.arch.models.get("default", {})
        # Resolve through af.auth rather than reading the environment. Reading
        # it directly meant `af auth add` could store a key and `af doctor`
        # could report READY while the run still died for want of a variable:
        # two credential systems, one of which the runner did not use.
        _credential(model)
        ctx.sink.emit("agent.start", agent_id="root", role="implement",
                      model=model.get("id"))

    def invoke(self, ctx: TrialContext) -> RunOutcome:
        arch, sink = ctx.arch, ctx.sink
        model = arch.models.get("default", {})
        budget = ctx.trial.budget
        max_turns = int(budget.get("max_turns", 40))
        max_tokens_budget = int(budget.get("max_tokens", 400_000))

        ctx_component = arch.slots.get("context", {}).get("component", "full-history")
        keep_last = int(arch.slots.get("context", {}).get("params", {}).get("keep_last", 12))

        messages = [{"role": "user", "content": _task_prompt(ctx)}]
        used = {"in": 0, "out": 0}
        reason = "finished"

        for turn in range(max_turns):
            if used["in"] + used["out"] > max_tokens_budget:
                reason = "budget"
                break

            sink.emit("model.request", phase="implement", turn=turn,
                      **{"gen_ai.request.model": model.get("id")})
            try:
                text, usage = _complete(model, SYSTEM, messages)
            except Exception as exc:  # noqa: BLE001
                sink.emit("error", message=f"provider error: {exc}")
                reason = "error"
                break

            used["in"] += usage.get("input_tokens", 0)
            used["out"] += usage.get("output_tokens", 0)
            sink.emit("model.response", phase="implement", turn=turn,
                      **{"gen_ai.usage.input_tokens": usage.get("input_tokens", 0),
                         "gen_ai.usage.output_tokens": usage.get("output_tokens", 0)})

            messages.append({"role": "assistant", "content": text})

            if "TASK_COMPLETE" in text:
                break

            cmd = _extract_command(text)
            if not cmd:
                messages.append({"role": "user",
                                 "content": "No command found. Emit one ```bash block."})
                continue

            phase = "verify" if _looks_like_test(cmd) else "implement"
            sink.emit("tool.call", **{"gen_ai.tool.name": "bash", "command": cmd},
                      phase=phase)
            result = ctx.sandbox.exec(ctx.handle, _shell(cmd), timeout=120)
            sink.emit("tool.result", exit_code=result.returncode, phase=phase,
                      bytes=len(result.stdout) + len(result.stderr),
                      passed=(result.returncode == 0) if phase == "verify" else None)

            obs = (result.stdout + result.stderr)[-6000:] or "(no output)"
            messages.append({"role": "user",
                             "content": f"exit={result.returncode}\n{obs}"})

            if ctx_component != "full-history" and len(messages) > keep_last * 2:
                head, tail = messages[:1], messages[-keep_last:]
                messages = head + [{"role": "user",
                                    "content": "[earlier turns compacted]"}] + tail
                sink.emit("context.compact", kept=len(messages))
        else:
            reason = "budget"

        sink.emit("agent.end", agent_id="root")
        total = used["in"] + used["out"]
        return RunOutcome(
            stopped_reason=reason,
            usage={
                "gen_ai.usage.input_tokens": used["in"],
                "gen_ai.usage.output_tokens": used["out"],
                "total_tokens": total,
                "cost_usd": round(_price(model, used), 4),
                "turns": turn + 1,
            },
            raw={"messages": len(messages)},
        )

    def collect(self, ctx: TrialContext) -> dict:
        return {"backend": self.name, "version": self.version}

    def normalize(self, raw: dict, sink) -> None:
        return None


# ------------------------------------------------------------------ helpers


def _credential(model: dict):
    """The one path to a key, with af.auth's stated precedence.

    Raises with the provider's own guidance rather than naming a single
    environment variable, because a stored credential is equally valid.
    """
    from af import auth

    provider = model.get("provider", "anthropic")
    cred = auth.resolve(provider)
    if not cred.available:
        raise RuntimeError(
            f"llm-bash needs a {provider} credential. Set one of "
            f"{', '.join(cred.provider.env) or '(none)'} or run "
            f"`af auth add --provider {provider} <key>`."
        )
    return cred


def _task_prompt(ctx: TrialContext) -> str:
    return (
        f"Repository is the current working directory.\n\n"
        f"TASK: {ctx.task.instruction}\n\n"
        f"Start by inspecting the code."
    )


def _extract_command(text: str) -> str | None:
    """The whole fenced block, not its first line.

    `_shell` runs the result through `sh -c`, which handles a heredoc, a loop
    or a multi-line edit perfectly well. Taking only the first line silently
    dropped the rest of what the model asked for and reported the truncated
    fragment's exit code as the result of the whole command.
    """
    m = re.search(r"```(?:bash|sh)?\s*\n(.*?)```", text, re.S)
    if not m:
        return None
    cmd = m.group(1).strip()
    return cmd or None


def _looks_like_test(cmd: str) -> bool:
    return any(t in cmd for t in ("unittest", "pytest", "test"))


def _shell(cmd: str) -> list[str]:
    if os.name == "nt":
        return ["cmd", "/c", cmd]
    return ["/bin/sh", "-c", cmd]


def _price(model: dict, used: dict) -> float:
    rates = {"anthropic": (3e-6, 15e-6), "openai": (2.5e-6, 10e-6)}
    ri, ro = rates.get(model.get("provider", ""), (3e-6, 15e-6))
    return used["in"] * ri + used["out"] * ro


def _complete(model: dict, system: str, messages: list[dict]) -> tuple[str, dict]:
    provider = model.get("provider", "anthropic")
    if provider == "anthropic":
        return _anthropic(model, system, messages)
    if provider == "openai":
        return _openai(model, system, messages)
    raise RuntimeError(f"llm-bash does not support provider {provider!r}")


def _post(url: str, headers: dict, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode())


def _anthropic(model: dict, system: str, messages: list[dict]) -> tuple[str, dict]:
    effort_budget = {"low": 0, "medium": 4000, "high": 12000, "xhigh": 24000}
    body: dict = {
        "model": model["id"],
        "max_tokens": 8000,
        "system": system,
        "messages": messages,
    }
    tb = effort_budget.get(model.get("effort", "medium"), 0)
    if tb:
        body["max_tokens"] = tb + 4000
        body["thinking"] = {"type": "enabled", "budget_tokens": tb}
    data = _post(
        "https://api.anthropic.com/v1/messages",
        {
            "content-type": "application/json",
            "x-api-key": _credential(model).key,
            "anthropic-version": "2023-06-01",
        },
        body,
    )
    text = "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text")
    u = data.get("usage", {})
    return text, {"input_tokens": u.get("input_tokens", 0),
                  "output_tokens": u.get("output_tokens", 0)}


def _openai(model: dict, system: str, messages: list[dict]) -> tuple[str, dict]:
    data = _post(
        "https://api.openai.com/v1/chat/completions",
        {"content-type": "application/json",
         "authorization": f"Bearer {_credential(model).key}"},
        {"model": model["id"],
         "messages": [{"role": "system", "content": system}, *messages]},
    )
    text = data["choices"][0]["message"]["content"]
    u = data.get("usage", {})
    return text, {"input_tokens": u.get("prompt_tokens", 0),
                  "output_tokens": u.get("completion_tokens", 0)}


register_backend(LlmBashBackend())
