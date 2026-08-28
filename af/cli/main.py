"""af - the AgentFoundry CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from af import ANALYSIS_VERSION, FOUNDRY_VERSION
from af.evidence import Bundle, iter_bundles, load_bundle
from af.spec.models import ArchitectureSpec, EnvironmentSpec, ExperimentSpec, TaskSpec, TrialSpec
from af.spec.operators import apply_operators, diff_architectures
from af.spec.registry import REGISTRY, SLOTS, resolve
from af.store import Store, reindex
from af.util import Paths, project_root, short

# ------------------------------------------------------------------ output

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

_COLOR = sys.stdout.isatty()

try:  # Windows consoles default to cp1252 and choke on box drawing
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def c(text: str, code: str) -> str:
    return f"{code}{text}{RESET}" if _COLOR else text


def out(*a) -> None:
    print(*a)


def emit(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, default=str))


def rule(title: str = "") -> None:
    out(c("─" * 68, DIM))
    if title:
        out(c(title, BOLD))
        out(c("─" * 68, DIM))


VERDICT_COLOR = {
    "BETTER": GREEN, "WORSE": RED, "EQUIVALENT": CYAN,
    "INCONCLUSIVE": YELLOW, "UNINTERPRETABLE": RED,
}


# ------------------------------------------------------------------ context


class Ctx:
    def __init__(self, args):
        self.root = project_root(Path(args.root) if args.root else None)
        self.paths = Paths(self.root)
        self.json = getattr(args, "json", False)
        self.sandbox = getattr(args, "sandbox", "local")
        self._store = None

    @property
    def store(self) -> Store:
        if self._store is None:
            self.paths.ensure()
            self._store = Store(self.paths.db)
        return self._store

    def sync_registry(self) -> None:
        """Load architectures and tasks from disk into the control plane."""
        for p in sorted(self.paths.architectures.glob("*.yaml")):
            self.store.put_architecture(ArchitectureSpec.load(p))
        for p in sorted(self.paths.tasks.glob("**/task.yaml")):
            self.store.put_task(TaskSpec.load(p.parent))


# ------------------------------------------------------------------ commands


def cmd_init(args) -> int:
    ctx = Ctx(args)
    ctx.paths.ensure()
    ctx.store  # create db
    out(f"initialized AgentFoundry at {c(str(ctx.root), BOLD)}")
    out(f"  state    {ctx.paths.state}")
    out(f"  next     af demo scaffold")
    return 0


def cmd_demo_scaffold(args) -> int:
    from af.demo import scaffold

    ctx = Ctx(args)
    ctx.paths.ensure()
    made = scaffold(ctx.root, n_tasks=args.tasks)
    ctx.sync_registry()
    out(f"scaffolded {len(made['tasks'])} tasks, "
        f"{len(made['architectures'])} architectures, "
        f"{len(made['experiments'])} experiments")
    out(f"  tasks         {', '.join(made['tasks'][:4])} ...")
    out(f"  architectures {', '.join(made['architectures'])}")
    out(f"  experiments   {', '.join(made['experiments'])}")
    out("")
    out(f"  next  {c('af task validate --all', BOLD)}")
    return 0


def cmd_demo_run(args) -> int:
    """The whole pipeline in one command."""
    from af.demo import scaffold

    ctx = Ctx(args)
    ctx.paths.ensure()
    rule("1/6  scaffold")
    scaffold(ctx.root, n_tasks=args.tasks)
    ctx.sync_registry()
    out(f"  {args.tasks} tasks, {len(list(ctx.paths.architectures.glob('*.yaml')))} architectures")

    rule("2/6  validate tasks")
    args.all, args.task = True, None
    cmd_task_validate(args)

    rule("3/6  plan experiment")
    args.spec = str(ctx.paths.experiments / f"{args.experiment}.yaml")
    cmd_exp_plan(args)

    rule("4/6  run trials")
    cmd_exp_start(args)

    rule("5/6  score")
    args.experiment_ref = None
    args.regrade = False
    cmd_score(args)

    rule("6/6  verdict")
    spec = ExperimentSpec.load(Path(args.spec))
    args.experiment_ref = spec.hash
    cmd_exp_verdict(args)

    out("")
    out(c("pipeline complete.", BOLD))
    out(f"  af analyze cluster           failure distribution")
    out(f"  af search race --base minimal-bash")
    return 0


# ---- tasks


def cmd_task_validate(args) -> int:
    from af.grade import validate_task

    ctx = Ctx(args)
    ctx.sync_registry()
    if args.task:
        dirs = [Path(args.task)]
    else:
        dirs = [p.parent for p in sorted(ctx.paths.tasks.glob("**/task.yaml"))]
    if not dirs:
        out("no tasks found")
        return 1

    results = []
    n_ok = 0
    for d in dirs:
        task = TaskSpec.load(d)
        r = validate_task(task, ctx.sandbox)
        results.append(r)
        ok = r["admissible"]
        n_ok += int(ok)
        mark = c("PASS", GREEN) if ok else c("FAIL", RED)
        out(f"  {mark}  {task.id}")
        if not ok:
            for name, chk in r["checks"].items():
                if not chk.get("pass"):
                    out(f"          {c('x ' + name, RED)}  {json.dumps(chk)}")
        ctx.store.put_task(task, ok)
    out(f"  {n_ok}/{len(dirs)} tasks admissible")
    emit(results, ctx.json)
    return 0 if n_ok == len(dirs) else 1


def cmd_task_list(args) -> int:
    ctx = Ctx(args)
    ctx.sync_registry()
    rows = ctx.store.list_tasks(args.suite)
    for r in rows:
        adm = "?" if r["admissible"] is None else ("ok" if r["admissible"] else "BAD")
        out(f"  {r['id']:<28} {r['suite']:<10} d={r['difficulty']:<5} {adm}")
    out(f"  {len(rows)} tasks")
    emit(rows, ctx.json)
    return 0


# ---- architectures


def cmd_arch_list(args) -> int:
    ctx = Ctx(args)
    ctx.sync_registry()
    rows = ctx.store.list_architectures()
    for r in rows:
        m = json.loads(r["manifest"])
        out(f"  {short(r['hash']):<10} {r['name']:<20} "
            f"{c(m.get('description', ''), DIM)}")
    emit(rows, ctx.json)
    return 0


def cmd_arch_show(args) -> int:
    from af.exec import get_backend

    ctx = Ctx(args)
    ctx.sync_registry()
    rec = ctx.store.get_architecture(args.ref)
    if rec is None:
        out(f"no architecture {args.ref!r}")
        return 1
    arch = ArchitectureSpec.from_dict(json.loads(rec["manifest"]))
    r = resolve(arch, get_backend(arch.backend).capabilities())
    out(f"  {c(arch.name, BOLD)}  {short(arch.hash)}")
    out(f"  backend    {arch.backend}")
    for role, m in arch.models.items():
        out(f"  model.{role:<8} {m.provider}/{m.id} effort={m.effort}")
    for slot in SLOTS:
        b = arch.slots.get(slot)
        if b:
            out(f"  {slot:<12} {b.component} {c(json.dumps(b.params), DIM)}")
    out(f"  budget     {json.dumps(arch.budget.to_dict())}")
    out(f"  derived    capability={r.capability:+.3f} cost_mult={r.cost_mult:.2f} "
        f"tools={r.tool_count} stages={r.stages or ['-']}")
    emit(r.to_dict(), ctx.json)
    return 0


def cmd_arch_diff(args) -> int:
    ctx = Ctx(args)
    ctx.sync_registry()
    a = ctx.store.get_architecture(args.a)
    b = ctx.store.get_architecture(args.b)
    if not a or not b:
        out("architecture not found")
        return 1
    sa = ArchitectureSpec.from_dict(json.loads(a["manifest"]))
    sb = ArchitectureSpec.from_dict(json.loads(b["manifest"]))
    out(f"  {sa.name} -> {sb.name}")
    for line in diff_architectures(sa, sb):
        out(f"    {line}")
    return 0


def cmd_arch_apply(args) -> int:
    ctx = Ctx(args)
    ctx.sync_registry()
    rec = ctx.store.get_architecture(args.ref)
    if rec is None:
        out(f"no architecture {args.ref!r}")
        return 1
    base = ArchitectureSpec.from_dict(json.loads(rec["manifest"]))
    ops = json.loads(args.operators)
    new = apply_operators(base, ops if isinstance(ops, list) else [ops])
    ctx.store.put_architecture(new)
    from af.util import dump_yaml, write_atomic

    path = ctx.paths.architectures / f"{new.name}.yaml"
    write_atomic(path, dump_yaml(new.to_dict()))
    out(f"  {new.name}  {short(new.hash)}  -> {path}")
    for line in diff_architectures(base, new):
        out(f"    {line}")
    return 0


def cmd_components(args) -> int:
    ctx = Ctx(args)
    for slot in SLOTS:
        out(c(f"  {slot}", BOLD))
        for comp in REGISTRY.list(slot):
            out(f"    {comp.name:<24} cap={comp.capability:+.2f} "
                f"cost x{comp.cost_mult:.2f} {c(' '.join(comp.tags), DIM)}")
    return 0


# ---- run


def cmd_run(args) -> int:
    from af.exec import get_backend, run_trial

    ctx = Ctx(args)
    ctx.sync_registry()
    rec = ctx.store.get_architecture(args.arch)
    if rec is None:
        out(f"no architecture {args.arch!r}")
        return 1
    arch = ArchitectureSpec.from_dict(json.loads(rec["manifest"]))
    task_dirs = {p.parent.name: p.parent
                 for p in ctx.paths.tasks.glob("**/task.yaml")}
    match = next((d for n, d in task_dirs.items() if n == args.task), None)
    if match is None:
        out(f"no task {args.task!r}")
        return 1
    task = TaskSpec.load(match)
    env = EnvironmentSpec()
    resolved = resolve(arch, get_backend(arch.backend).capabilities())
    ts = TrialSpec(task.id, task.hash, arch.hash, env.hash, resolved.models,
                   resolved.budget, args.nonce)
    d = run_trial(ts, task, arch, env, ctx.paths.bundles, ctx.sandbox)
    b = Bundle(d)
    ctx.store.enqueue_trial(ts)
    ctx.store.finish_trial(ts.trial_key, "sealed", str(d), b.seal.get("error"))
    out(f"  trial {short(b.trial_key, 12)}  {b.seal.get('status')}  -> {d}")
    out(f"  {json.dumps(b.usage)}")
    if args.score:
        from af.grade import score_bundle

        s = score_bundle(b, task, ctx.sandbox, ANALYSIS_VERSION)
        ctx.store.put_score(s)
        b.write_derived("score.json", s.to_dict())
        out(f"  resolved={s.resolved} credibility={s.credibility} flags={s.flags}")
    return 0


# ---- experiments


def _load_spec(ctx: Ctx, ref: str) -> ExperimentSpec:
    p = Path(ref)
    if not p.exists():
        p = ctx.paths.experiments / f"{ref}.yaml"
    return ExperimentSpec.load(p)


def cmd_exp_plan(args) -> int:
    from af.experiment import plan_experiment

    ctx = Ctx(args)
    ctx.sync_registry()
    spec = _load_spec(ctx, args.spec)
    plan = plan_experiment(ctx.root, spec)
    out(f"  {c(spec.name, BOLD)}  {short(spec.hash)}")
    if spec.hypothesis:
        out(f"  hypothesis  {c(spec.hypothesis, DIM)}")
    out(f"  arms        {len(plan.arms)}   tasks {plan.n_tasks}   "
        f"replicates {spec.replicates}   trials {len(plan.trials)}")
    for aid, info in plan.arms.items():
        out(f"    {aid:<14} {info['architecture']:<18} "
            f"cap={info['capability']:+.3f} cost x{info['cost_mult']:.2f}")
    out(f"  est cost    ${plan.est_cost_usd:.2f}")
    out(f"  MDE         {plan.mde:.1%}  "
        f"{c('(smallest effect this design can detect)', DIM)}")
    for w in plan.warnings:
        out(f"  {c('warn', YELLOW)}  {w}")
    emit(plan.to_dict(), ctx.json)
    return 0


def cmd_exp_start(args) -> int:
    from af.experiment import Scheduler, plan_experiment

    ctx = Ctx(args)
    ctx.sync_registry()
    spec = _load_spec(ctx, args.spec)
    plan = plan_experiment(ctx.root, spec)
    sched = Scheduler(ctx.store, ctx.root, ctx.paths.bundles, ctx.sandbox)
    exp_hash = sched.submit(spec, plan)
    out(f"  frozen      {short(exp_hash)}   "
        f"{c('(this hash IS the preregistration)', DIM)}")
    out(f"  queued      {len(plan.trials)} trials")

    total = len(plan.trials)

    def progress(done, key):
        if done % max(1, total // 20) == 0 or done == total:
            pct = int(100 * done / total)
            sys.stdout.write(f"\r  running     {done}/{total}  {pct}%")
            sys.stdout.flush()

    res = sched.drain(exp_hash, on_progress=progress)
    sys.stdout.write("\r" + " " * 40 + "\r")
    out(f"  ran         {res['ran']} trials, ${res['spent_usd']:.2f}")
    counts = ctx.store.counts_by_state(exp_hash)
    out(f"  states      {json.dumps(counts)}")
    args.experiment_ref = exp_hash
    return 0


def cmd_exp_list(args) -> int:
    ctx = Ctx(args)
    for e in ctx.store.list_experiments():
        v = ctx.store.verdicts_for(e["hash"])
        res = v[0]["result"] if v else "-"
        out(f"  {short(e['hash']):<10} {e['name']:<30} {e['state']:<10} "
            f"{c(res, VERDICT_COLOR.get(res, DIM))}")
    return 0


def cmd_exp_watch(args) -> int:
    ctx = Ctx(args)
    e = ctx.store.get_experiment(args.experiment_ref)
    if e is None:
        out("no such experiment")
        return 1
    counts = ctx.store.counts_by_state(e["hash"])
    out(f"  {e['name']}  {short(e['hash'])}  state={e['state']}")
    out(f"  {json.dumps(counts)}")
    return 0


def cmd_score(args) -> int:
    from af.experiment import score_experiment

    ctx = Ctx(args)
    ctx.sync_registry()
    exp_hash = None
    if getattr(args, "experiment_ref", None):
        e = ctx.store.get_experiment(args.experiment_ref)
        exp_hash = e["hash"] if e else None
    res = score_experiment(ctx.store, ctx.root, exp_hash,
                           regrade=getattr(args, "regrade", False),
                           sandbox_kind=ctx.sandbox)
    out(f"  scored      {res['scored']} bundles "
        f"{c('(analysis_version=' + ANALYSIS_VERSION + ')', DIM)}")
    return 0


def cmd_exp_verdict(args) -> int:
    from af.experiment import analyze_experiment

    ctx = Ctx(args)
    e = ctx.store.get_experiment(args.experiment_ref)
    if e is None:
        out("no such experiment")
        return 1
    v = analyze_experiment(ctx.store, e["hash"])
    p = v["payload"]
    colour = VERDICT_COLOR.get(v["result"], DIM)
    out("")
    out(f"  {c(v['result'], colour + BOLD)}   {p['reason']}")
    out("")
    out(f"  {'arm':<16}{'resolve':<10}{'95% CI':<20}{'cost':<10}{'flags'}")
    for aid, a in p["arms"].items():
        ci = f"[{a['ci'][0]:+.2f},{a['ci'][1]:+.2f}]"
        flags = ",".join(f"{k}:{v}" for k, v in (a.get("flags") or {}).items()) or "-"
        out(f"  {aid:<16}{a['rate']:<10.3f}{ci:<20}${a['cost_usd_mean']:<9.3f}{c(flags, DIM)}")
    out("")
    for cmp in p["comparisons"]:
        sig = c("significant", GREEN) if cmp["significant_after_fdr"] else c("n.s.", DIM)
        eq = " EQUIVALENT" if cmp["equivalence"]["equivalent"] else ""
        out(f"  {cmp['baseline']} -> {cmp['candidate']}: "
            f"delta={cmp['delta']:+.3f} "
            f"CI[{cmp['ci'][0]:+.3f},{cmp['ci'][1]:+.3f}] "
            f"p={cmp['p_value']:.3f} {sig}{c(eq, CYAN)} "
            f"cost{cmp['cost_delta_usd']:+.3f}")
    out("")
    out(f"  credibility {json.dumps(p['credibility'])}")
    out(f"  n_tasks={p['n_tasks']}  MDE={p['minimum_detectable_effect']:.1%}  "
        f"margin={p['equivalence_margin']}")
    out(f"  verdict     {short(v['hash'])}  "
        f"{c('(immutable; re-analysis creates a new one)', DIM)}")
    emit(v, ctx.json)
    return 0


# ---- analysis


def _bundles_and_scores(ctx: Ctx):
    bundles = [b for b in iter_bundles(ctx.paths.bundles) if b.ok]
    scores = ctx.store.get_scores([b.trial_key for b in bundles], ANALYSIS_VERSION)
    return bundles, scores


def cmd_analyze_cluster(args) -> int:
    from af.analyze import cluster_failures, findings_from_clusters

    ctx = Ctx(args)
    bundles, scores = _bundles_and_scores(ctx)
    res = cluster_failures(bundles, scores)
    out(f"  {res['n_failures']} failures over {len(bundles)} trials")
    for cl in res["clusters"]:
        bar = "#" * max(1, int(cl["share"] * 40))
        out(f"    {cl['code']:<26} {cl['n']:>4}  {cl['share']:>5.1%}  {c(bar, DIM)}")
        out(f"      {c(cl['description'], DIM)}")
    findings = findings_from_clusters(res)
    for f in findings:
        ctx.store.put_finding(f)
    out(f"  {len(findings)} findings recorded")
    out(f"  {c('calibration: ' + res['calibration']['note'][:60] + '...', YELLOW)}")
    emit(res, ctx.json)
    return 0


def cmd_analyze_autopsy(args) -> int:
    from af.analyze import autopsy

    ctx = Ctx(args)
    b = load_bundle(ctx.paths.bundles, args.trial)
    score = ctx.store.get_scores([b.trial_key], ANALYSIS_VERSION).get(b.trial_key)
    a = autopsy(b, score)
    out(f"  trial       {short(b.trial_key, 12)}")
    out(f"  task        {a['task']}   arch {a['architecture']}")
    out(f"  outcome     {json.dumps(a['outcome'])}")
    out(f"  credibility {a['credibility']}  flags={a['flags']}")
    out("")
    for d in a["diagnoses"]:
        out(f"    {c(d['code'], BOLD)}  conf={d['confidence']}")
        out(f"      {d['description']}")
        out(f"      {c(d['note'], DIM)}")
        if d["evidence_seq"]:
            out(f"      evidence: seq {d['evidence_seq']}")
    out("")
    out(f"  {c('UNCALIBRATED - confidences are provisional', YELLOW)}")
    emit(a, ctx.json)
    return 0


def cmd_analyze_landscape(args) -> int:
    from af.analyze import build_landscape

    ctx = Ctx(args)
    bundles, scores = _bundles_and_scores(ctx)
    if args.task:
        bundles = [b for b in bundles if b.manifest["task"]["id"] == args.task]
    l = build_landscape(bundles, scores)
    out(f"  {len(l['nodes'])} states from {len(bundles)} trials")
    out(c("  traps (states that mostly end in failure)", BOLD))
    for n in l["traps"]:
        out(f"    {n['state']:<34} visits={n['visits']:<5} succ={n['success_rate']:.2f}")
    out(c("  productive cores", BOLD))
    for n in l["productive"]:
        out(f"    {n['state']:<34} visits={n['visits']:<5} succ={n['success_rate']:.2f}")
    emit(l, ctx.json)
    return 0


def cmd_analyze_index(args) -> int:
    from af.analyze import index_bundle

    ctx = Ctx(args)
    bundles, _ = _bundles_and_scores(ctx)
    rows = [index_bundle(b).to_dict() for b in bundles]
    out(f"  indexed {len(rows)} trajectories")
    emit(rows, ctx.json)
    return 0


def cmd_findings(args) -> int:
    ctx = Ctx(args)
    for f in ctx.store.list_findings():
        out(f"  {f['id']}  {f['label']:<26} conf={f['confidence']}  "
            f"{len(f['evidence'])} evidence")
    return 0


def cmd_replay(args) -> int:
    from af.analyze import replay_plan

    ctx = Ctx(args)
    b = load_bundle(ctx.paths.bundles, args.trial)
    p = replay_plan(b, args.step, args.n, args.intervention)
    out(f"  {p['intervention']} at seq {p['step']} x{p['replicates']}")
    out(f"  event       {json.dumps(p['event'])[:160]}")
    out(f"  est cost    ${p['estimated_cost_usd']:.4f}")
    out(f"  {c(p['note'], YELLOW)}")
    emit(p, ctx.json)
    return 0


def cmd_trials(args) -> int:
    ctx = Ctx(args)
    rows = [b.summary() for b in iter_bundles(ctx.paths.bundles)]
    for r in rows[: args.limit]:
        out(f"  {short(r['trial_key'], 12)}  {r['task']:<24} {r['arch']}  "
            f"{r['status']:<7} {r['wall_s']}s  ${r['cost_usd']}")
    out(f"  {len(rows)} bundles")
    emit(rows, ctx.json)
    return 0


# ---- search


def cmd_search_race(args) -> int:
    from af.exec import get_backend
    from af.experiment import load_suite
    from af.search import PROPOSERS, Race, SearchContext, niche_of
    from af.spec.registry import resolve as resolve_arch
    from af.exec import run_trial
    from af.grade import score_bundle

    ctx = Ctx(args)
    ctx.sync_registry()
    rec = ctx.store.get_architecture(args.base)
    if rec is None:
        out(f"no architecture {args.base!r}")
        return 1
    base = ArchitectureSpec.from_dict(json.loads(rec["manifest"]))

    findings = ctx.store.list_findings()
    sctx = SearchContext(base=base, findings=findings)
    proposals = []
    for name in args.proposers.split(","):
        p = PROPOSERS.get(name.strip())
        if p:
            proposals.extend(p.propose(sctx))
    if not proposals:
        out("no proposals")
        return 1

    race = Race(base, proposals, alpha=args.alpha)
    tasks = load_suite(ctx.root, args.suite)
    env = EnvironmentSpec()
    out(f"  base        {base.name}  {short(base.hash)}")
    out(f"  candidates  {len(race.candidates)}")
    out(f"  tasks       {len(tasks)}   rounds {args.rounds}  alpha {args.alpha}")
    out("")

    per_round = max(1, len(tasks) // max(1, args.rounds))
    for rnd in range(args.rounds):
        batch = tasks[rnd * per_round:(rnd + 1) * per_round] or tasks[:per_round]
        for cand in race.alive():
            ctx.store.put_architecture(cand.arch)
            backend = get_backend(cand.arch.backend)
            r = resolve_arch(cand.arch, backend.capabilities())
            for task in batch:
                ts = TrialSpec(task.id, task.hash, cand.arch.hash, env.hash,
                               r.models, r.budget, rnd)
                d = run_trial(ts, task, cand.arch, env, ctx.paths.bundles, ctx.sandbox)
                b = Bundle(d)
                s = score_bundle(b, task, ctx.sandbox, ANALYSIS_VERSION)
                ctx.store.put_score(s)
                race.record(cand, task.id, s.resolved,
                            float(b.usage.get("cost_usd") or 0))
        killed = race.eliminate(rnd)
        out(f"  round {rnd}: {len(race.alive())} alive"
            + (f", eliminated {[k.arch.name for k in killed]}" if killed else ""))
        if len(race.alive()) <= 1:
            break

    rep = race.report()
    out("")
    out(f"  {'candidate':<34}{'rate':<8}{'cost':<10}{'n':<5}status")
    for row in rep["candidates"]:
        status = c("alive", GREEN) if row["alive"] else c(f"out@r{row['eliminated_at']}", DIM)
        out(f"  {row['name'][:32]:<34}{row['rate']:<8.3f}${row['cost']:<9.3f}"
            f"{row['n']:<5}{status}")
    out("")
    for cand in race.alive():
        r = resolve_arch(cand.arch, get_backend(cand.arch.backend).capabilities())
        ctx.store.put_archive(cand.arch.hash, niche_of(cand.arch, r), cand.rate,
                              cand.mean_cost, {"name": cand.arch.name,
                                               "operators": cand.proposal.operators})
    out(f"  {c(rep['note'], YELLOW)}")
    emit(rep, ctx.json)
    return 0


def cmd_search_archive(args) -> int:
    ctx = Ctx(args)
    rows = ctx.store.list_archive()
    out(f"  {'niche':<34}{'rate':<8}{'cost':<10}architecture")
    for r in rows:
        out(f"  {r['niche']:<34}{r['score']:<8.3f}${r['cost']:<9.3f}"
            f"{r['payload'].get('name')}")
    emit(rows, ctx.json)
    return 0


# ---- misc


def cmd_reindex(args) -> int:
    ctx = Ctx(args)
    res = reindex(ctx.store, ctx.paths.bundles, ctx.paths.tasks, ctx.paths.architectures)
    out(f"  rebuilt control plane from evidence: {json.dumps(res)}")
    return 0


def cmd_backends(args) -> int:
    from af.exec import conformance_check, get_backend, list_backends

    ctx = Ctx(args)
    for name in list_backends():
        b = get_backend(name)
        problems = conformance_check(b)
        mark = c("ok", GREEN) if not problems else c("FAIL", RED)
        out(f"  {name:<14} v{b.version:<8} {mark}  "
            f"{len(b.capabilities())} capabilities")
        for p in problems:
            out(f"      {c(p, RED)}")
    return 0


def cmd_doctor(args) -> int:
    from af.doctor import CORE, FAIL, PASS, REAL, SIM, SKIP, WARN, run_all, summarize

    ctx = Ctx(args)
    tiers = tuple(t.strip() for t in args.tier.split(",")) if args.tier else (CORE, SIM, REAL)
    checks = run_all(ctx.root, tiers)
    colours = {PASS: GREEN, WARN: YELLOW, FAIL: RED, SKIP: DIM}
    labels = {CORE: "core (run anything)",
              SIM: "simulator (af demo run)",
              REAL: "real runs (live agent on a real repo)"}

    for tier in (CORE, SIM, REAL):
        group = [c for c in checks if c.tier == tier]
        if not group:
            continue
        out("")
        out(c(f"  {labels[tier]}", BOLD))
        for chk in group:
            mark = c(f"{chk.status:<4}", colours.get(chk.status, DIM))
            out(f"    {mark} {chk.name:<24} {c(chk.detail, DIM)}")
            if chk.remedy:
                out(f"         {c('-> ' + chk.remedy, YELLOW)}")

    s = summarize(checks)
    out("")
    for name, ok in s["ready"].items():
        if ok is None:
            out(f"  {name:<12} {c('not checked', DIM)}")
            continue
        mark = c("READY", GREEN) if ok else c("BLOCKED", RED)
        out(f"  {name:<12} {mark}")
    out("")
    res = next((c2 for c2 in checks if c2.name == "host resources"), None)
    if res and res.extra.get("suggested_workers"):
        out(f"  {c('suggested --workers ' + str(res.extra['suggested_workers']), DIM)}")
    emit([chk.to_dict() for chk in checks] + [s], ctx.json)
    return 0 if s["ready"]["simulator"] else 1


def cmd_serve(args) -> int:
    from af.api import serve

    ctx = Ctx(args)
    serve(ctx.root, args.port)
    return 0


def cmd_version(args) -> int:
    out(f"agentfoundry {FOUNDRY_VERSION} (analysis v{ANALYSIS_VERSION})")
    return 0


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="af", description="AgentFoundry")
    p.add_argument("--root", help="project root")
    p.add_argument("--json", action="store_true", help="also dump JSON")
    p.add_argument("--sandbox", default="local", choices=["local", "docker"])
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("init").set_defaults(fn=cmd_init)
    sub.add_parser("version").set_defaults(fn=cmd_version)
    sub.add_parser("components").set_defaults(fn=cmd_components)
    sub.add_parser("backends").set_defaults(fn=cmd_backends)
    sub.add_parser("reindex").set_defaults(fn=cmd_reindex)
    doc = sub.add_parser("doctor")
    doc.add_argument("--tier", help="core,simulator,real (default: all)")
    doc.set_defaults(fn=cmd_doctor)
    sub.add_parser("findings").set_defaults(fn=cmd_findings)

    d = sub.add_parser("demo").add_subparsers(dest="sub")
    ds = d.add_parser("scaffold")
    ds.add_argument("--tasks", type=int, default=12)
    ds.set_defaults(fn=cmd_demo_scaffold)
    dr = d.add_parser("run")
    dr.add_argument("--tasks", type=int, default=12)
    dr.add_argument("--experiment", default="review-stage")
    dr.set_defaults(fn=cmd_demo_run)

    t = sub.add_parser("task").add_subparsers(dest="sub")
    tv = t.add_parser("validate")
    tv.add_argument("task", nargs="?")
    tv.add_argument("--all", action="store_true")
    tv.set_defaults(fn=cmd_task_validate)
    tl = t.add_parser("list")
    tl.add_argument("--suite")
    tl.set_defaults(fn=cmd_task_list)

    a = sub.add_parser("arch").add_subparsers(dest="sub")
    a.add_parser("list").set_defaults(fn=cmd_arch_list)
    ash = a.add_parser("show")
    ash.add_argument("ref")
    ash.set_defaults(fn=cmd_arch_show)
    ad = a.add_parser("diff")
    ad.add_argument("a")
    ad.add_argument("b")
    ad.set_defaults(fn=cmd_arch_diff)
    aa = a.add_parser("apply")
    aa.add_argument("ref")
    aa.add_argument("operators", help='JSON, e.g. \'[{"op":"AddStage","stage":"review"}]\'')
    aa.set_defaults(fn=cmd_arch_apply)

    r = sub.add_parser("run")
    r.add_argument("--task", required=True)
    r.add_argument("--arch", required=True)
    r.add_argument("--nonce", type=int, default=0)
    r.add_argument("--score", action="store_true")
    r.set_defaults(fn=cmd_run)

    e = sub.add_parser("exp").add_subparsers(dest="sub")
    ep = e.add_parser("plan")
    ep.add_argument("spec")
    ep.set_defaults(fn=cmd_exp_plan)
    es = e.add_parser("start")
    es.add_argument("spec")
    es.set_defaults(fn=cmd_exp_start)
    e.add_parser("list").set_defaults(fn=cmd_exp_list)
    ew = e.add_parser("watch")
    ew.add_argument("experiment_ref")
    ew.set_defaults(fn=cmd_exp_watch)
    ev = e.add_parser("verdict")
    ev.add_argument("experiment_ref")
    ev.set_defaults(fn=cmd_exp_verdict)

    sc = sub.add_parser("score")
    sc.add_argument("experiment_ref", nargs="?")
    sc.add_argument("--regrade", action="store_true")
    sc.set_defaults(fn=cmd_score)

    an = sub.add_parser("analyze").add_subparsers(dest="sub")
    ac = an.add_parser("cluster")
    ac.set_defaults(fn=cmd_analyze_cluster)
    au = an.add_parser("autopsy")
    au.add_argument("trial")
    au.set_defaults(fn=cmd_analyze_autopsy)
    al = an.add_parser("landscape")
    al.add_argument("--task")
    al.set_defaults(fn=cmd_analyze_landscape)
    ai = an.add_parser("index")
    ai.set_defaults(fn=cmd_analyze_index)

    rp = sub.add_parser("replay")
    rp.add_argument("trial")
    rp.add_argument("--step", type=int, required=True)
    rp.add_argument("--n", type=int, default=8)
    rp.add_argument("--intervention", default="do_resample")
    rp.set_defaults(fn=cmd_replay)

    tr = sub.add_parser("trials")
    tr.add_argument("--limit", type=int, default=40)
    tr.set_defaults(fn=cmd_trials)

    s = sub.add_parser("search").add_subparsers(dest="sub")
    sr = s.add_parser("race")
    sr.add_argument("--base", default="minimal-bash")
    sr.add_argument("--suite", default="demo")
    sr.add_argument("--rounds", type=int, default=3)
    sr.add_argument("--alpha", type=float, default=0.10)
    sr.add_argument("--proposers", default="mutation,finding-driven")
    sr.set_defaults(fn=cmd_search_race)
    s.add_parser("archive").set_defaults(fn=cmd_search_archive)

    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8787)
    sv.set_defaults(fn=cmd_serve)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    fn = getattr(args, "fn", None)
    if fn is None:
        parser.print_help()
        return 1
    try:
        return fn(args) or 0
    except KeyboardInterrupt:
        out("\ninterrupted")
        return 130
    except Exception as exc:  # noqa: BLE001
        out(c(f"error: {exc}", RED))
        if "--debug" in (argv or sys.argv):
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
