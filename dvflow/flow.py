"""Flow: loads the config and turns the command line into a list of Jobs.

Planning only - nothing is run here (see runner.py). The main entry points:

  Flow(args)        load and validate the config
  Flow.plan()       -> [build job, test job, test job, ...]
  Flow.list()       print phases, models and tests (--list)
"""

import copy
import datetime
import getpass
import glob
import os
import re
import sys
from pathlib import Path

from .constants import (DEFAULT_JOB_SHELL, DEFAULT_SLURM, DEFAULT_WAVE_FILE, DEFAULT_WAVES, JOB_SHELLS, NO_TEST,
                        PHASE_SCOPES, SLURM_MODES, SUBPHASE_KEYS, SUBPHASES)
from .errors import FlowError
from .jobs import Job, Step
from .phase_args import merge_phase_args
from .templates import YamlTemplate
from .testlist import load_testlist, seeds_for, select_tests
from .utils import deep_merge, join_args, parse_defines, slurm_time, split_csv, to_cmdline

# Directory of dv_flow.py (the package's parent); scripts/ lives there.
FLOW_DIR = Path(__file__).resolve().parent.parent


class Flow:
    def __init__(self, args):
        self.args = args
        self.now = datetime.datetime.now()
        self.time = self.now.strftime("%Y%m%d_%H%M%S")   # one timestamp for the whole run
        self.defines = parse_defines(args.define)
        self.include_dirs = args.include_dir or ()
        self.cfg_tpl = YamlTemplate(args.config, self.include_dirs)
        self.config_dir = self.cfg_tpl.path.parent

        # Names available in every config and test list, in addition to `variables:`.
        self.base_ctx = {
            "env": dict(os.environ),
            "config_dir": self.config_dir.as_posix(),
            "cwd": Path.cwd().as_posix(),
            "user": getpass.getuser(),
            "time": self.time,
            "date": self.now.strftime("%Y-%m-%d"),
            "gui": False,
            "mode": "local",
            "python": Path(sys.executable).as_posix(),   # the Python running dv_flow
            "flow_dir": FLOW_DIR.as_posix(),             # where dv_flow.py and scripts/ are
            "dump_waves": False,                         # dump a VPD in this test job
            "wave_path": "",                             # the wave file being opened (--open-waves)
            "wave_dir": "",
        }
        # "Discovery" render: no specific test or seed yet. Used to find the
        # phases, the models and the test list; every job renders again later.
        self.cfg, self.disc_ctx = self.render_config(model=args.model or "")
        self.phases = self.parse_phases(self.cfg.get("phases"))
        self.models = self.cfg.get("models") or {}
        if not isinstance(self.models, dict) or not self.models:
            raise FlowError(f"{self.cfg_tpl.path}: no 'models' defined")

    # ------------------------------------------------------------------ config
    def render_config(self, model, test=None, seed=0, gui=False, mode="local", coverage=False,
                      dump_waves=False, extra=None):
        """Render the config for one job. Returns (data, context).

        `extra` adds names to the context (the wave viewer uses wave_path / wave_dir).
        """
        ctx = {**self.base_ctx, "model": model, "test": test or NO_TEST, "seed": seed,
               "gui": gui, "mode": mode, "coverage": coverage, "dump_waves": dump_waves, **(extra or {})}
        return self.cfg_tpl.render(ctx, self.defines)

    @staticmethod
    def parse_phases(raw):
        """Validate the `phases:` section -> {name: {scope, description, test_args, fail_patterns}}.

        Accepts a mapping (normal form) or a plain list of names.
        """
        if not raw:
            raise FlowError("config: no 'phases' defined")
        if isinstance(raw, list):
            raw = {(p if isinstance(p, str) else next(iter(p))): (None if isinstance(p, str) else next(iter(p.values())))
                   for p in raw}
        phases = {}
        for name, spec in raw.items():
            spec = spec or {}
            scope = spec.get("scope", "test")
            if scope not in PHASE_SCOPES:
                raise FlowError(f"phase '{name}': scope must be one of {PHASE_SCOPES}, got '{scope}'")
            test_args = to_cmdline(spec.get("test_args"))
            if test_args and scope != "test":
                raise FlowError(f"phase '{name}': test_args is only allowed on scope: test phases")
            fail = spec.get("fail_patterns") or []
            fail = [str(x) for x in ([fail] if isinstance(fail, str) else fail)]
            for pat in fail:
                try:
                    re.compile(pat)
                except re.error as exc:
                    raise FlowError(f"phase '{name}': bad fail_patterns regex '{pat}': {exc}") from None
            phases[name] = {"scope": scope, "description": spec.get("description", ""),
                            "test_args": test_args, "fail_patterns": fail}
        return phases

    def resolve_model(self, models, name, _seen=None):
        """A model with its `extends:` chain merged in (parent first, child wins)."""
        _seen = _seen or []
        if name not in models:
            visible = [m for m, v in models.items() if not (v or {}).get("abstract")]
            raise FlowError(f"unknown model '{name}' (available: {', '.join(visible)})")
        if name in _seen:
            raise FlowError(f"model inheritance loop: {' -> '.join(_seen + [name])}")
        spec = copy.deepcopy(models[name] or {})
        parent = spec.pop("extends", None)
        if parent:
            spec = deep_merge(self.resolve_model(models, parent, _seen + [name]), spec)
        return spec

    def model_cfg(self, cfg, name):
        """Resolved model, checked for unknown phases, sub-phases and keys."""
        spec = self.resolve_model(cfg.get("models") or {}, name)
        for phase, subs in (spec.get("phases") or {}).items():
            if phase not in self.phases:
                raise FlowError(f"model '{name}': phase '{phase}' is not declared in the 'phases' section")
            for sub, entry in (subs or {}).items():
                if sub == "files":           # files written into the job directory: name -> content
                    if entry is not None and not isinstance(entry, dict):
                        raise FlowError(f"model '{name}' phase '{phase}': files must be a mapping name -> content")
                    continue
                if sub not in SUBPHASES:
                    raise FlowError(f"model '{name}' phase '{phase}': unknown sub-phase '{sub}' "
                                    f"(expected {', '.join(SUBPHASES)})")
                bad = set(entry or {}) - set(SUBPHASE_KEYS)
                if bad:
                    raise FlowError(f"model '{name}' {phase}.{sub}: unknown keys {sorted(bad)} "
                                    f"(expected {', '.join(SUBPHASE_KEYS)})")
        return spec

    def slurm_cfg(self, cfg, model_spec, extra=None, test=None):
        """Slurm settings for a job, lowest priority first:

        defaults < config `slurm:` < model `slurm:` < `extra` (the `waves.slurm:`
        block for the wave viewer) < testcase slurm_mem < command line.
        """
        scfg = deep_merge(DEFAULT_SLURM, cfg.get("slurm") or {})
        scfg = deep_merge(scfg, model_spec.get("slurm") or {})
        scfg = deep_merge(scfg, extra or {})
        if test and test.get("slurm_mem"):
            scfg["mem"] = str(test["slurm_mem"])
        a = self.args
        if a.slurm is not None:
            scfg["enable"] = a.slurm
        if a.mode:
            scfg["mode"] = a.mode
        elif a.wait and not extra:                       # --wait tracks sbatch jobs: it implies batch
            scfg["mode"] = "batch"
        if a.gui:
            scfg["gui"] = True
        if a.slurm_mem:
            scfg["mem"] = a.slurm_mem
        if a.slurm_opt:                                  # --slurm-opt: appended to the job's options
            scfg["options"] = [*(scfg.get("options") or []), *a.slurm_opt]
        if scfg["mode"] not in SLURM_MODES:
            raise FlowError(f"slurm.mode must be one of {SLURM_MODES}, got '{scfg['mode']}'")
        return scfg

    # --------------------------------------------------------------- selection
    def select_phases(self):
        """--phase list -> phase names, always in the order of the `phases:` section."""
        req = split_csv(self.args.phase)
        if not req or "all" in req:
            return list(self.phases)
        unknown = [p for p in req if p not in self.phases]
        if unknown:
            raise FlowError(f"unknown phase(s): {', '.join(unknown)} (available: {', '.join(self.phases)}, all)")
        return [p for p in self.phases if p in req]

    def testlist_path(self, model):
        path = self.model_cfg(self.cfg, model).get("testlist")
        if not path:
            raise FlowError(f"model '{model}' has no 'testlist' entry")
        path = Path(path)
        return path if path.is_absolute() else self.config_dir / path

    def load_tests(self, model):
        """(tests, sections) of a model's test list."""
        return load_testlist(self.testlist_path(model), {**self.disc_ctx, "model": model},
                             self.defines, self.include_dirs)

    # ----------------------------------------------------------------- planning
    def job_shell(self, cfg):
        """Shell of the job scripts that are run: --shell, else `job_shell:`, else tcsh."""
        shell = self.args.shell or cfg.get("job_shell") or DEFAULT_JOB_SHELL
        if shell not in JOB_SHELLS:
            raise FlowError(f"job_shell must be one of {JOB_SHELLS}, got '{shell}'")
        return shell

    def make_job(self, kind, model, phases, test, seed, phase_args, coverage=False, dump_waves=False):
        """Build one Job: render the config for this test/seed and assemble each step's command."""
        scfg0 = self.slurm_cfg(self.cfg, self.model_cfg(self.cfg, model), test=test)
        mode = scfg0["mode"] if scfg0["enable"] else "local"
        cfg, ctx = self.render_config(model, test=test, seed=seed, gui=bool(scfg0["gui"]), mode=mode,
                                      coverage=coverage, dump_waves=dump_waves)
        spec = self.model_cfg(cfg, model)
        scfg = self.slurm_cfg(cfg, spec, test=test)
        variables = ctx["variables"]

        # Job directory: build_dir for the build job, run_dir for test jobs.
        dir_key = "build_dir" if kind == "build" else "run_dir"
        directory = variables.get(dir_key) or spec.get(dir_key)
        if not directory:
            default = f"out/{model}/" + ("build" if kind == "build" else f"{test['name']}.{seed}")
            directory = (self.config_dir / default).as_posix()
        directory = Path(directory)
        if not directory.is_absolute():
            directory = self.config_dir / directory

        name_tpl = scfg["build_job_name"] if kind == "build" else scfg["job_name"]
        name = self.cfg_tpl.render_string(name_tpl, ctx)

        steps, files = [], {}
        model_phases = spec.get("phases") or {}
        phase_defs = self.parse_phases(cfg.get("phases"))  # rendered for this test/seed
        for phase in phases:
            if phase not in model_phases:
                print(f"dv_flow: warning: model '{model}' does not define phase '{phase}', skipping")
                continue
            # files: of the phase (already rendered for this job), e.g. the wave dump script
            for fname, content in ((model_phases[phase] or {}).get("files") or {}).items():
                files[str(fname)] = "" if content is None else str(content)
            for sub in SUBPHASES:
                entry = (model_phases[phase] or {}).get(sub) or {}

                def field_value(fld):
                    """cmd/opts/args from the config, with phase-args edits applied in order."""
                    val = to_cmdline(entry.get(fld))
                    for op, new in phase_args.get(f"{phase}.{sub}.{fld}", []):
                        new = self.cfg_tpl.render_string(new, ctx)
                        val = join_args(val, new) if op == "+=" else new
                    return val

                cmd = field_value("cmd")
                extra = self.cfg_tpl.render_string(phase_args.get(f"{phase}.{sub}", ""), ctx)
                if sub == "phase" and kind == "test":
                    # the phase's test_args (e.g. +UVM_TESTNAME=<testname>) come before the phase args
                    extra = join_args(phase_defs[phase]["test_args"], extra)
                if not cmd:
                    if extra:
                        print(f"dv_flow: warning: args for {phase}.{sub} ignored (no cmd defined)")
                    continue
                # The command line of every step: cmd opts args <test_args> <phase args>
                steps.append(Step(phase, sub, join_args(cmd, field_value("opts"), field_value("args"), extra),
                                  phase_defs[phase]["fail_patterns"]))
        return Job(name=name, kind=kind, model=model, directory=directory, phases=phases,
                   steps=steps, slurm=scfg, test=test or {}, seed=seed,
                   timeout=self.job_timeout(kind, test or {}, scfg), files=files, shell=self.job_shell(cfg))

    def job_timeout(self, kind, test, scfg):
        """Slurm --time: --slurm-timeout > testcase slurm_timeout > slurm.timeout (build: build_timeout,
        wave viewer: waves.slurm.timeout)."""
        if kind == "build":
            value = scfg.get("build_timeout") or scfg.get("timeout")
            return slurm_time(value, "slurm.build_timeout") if value else None
        if kind == "view":
            value = self.args.slurm_timeout or scfg.get("timeout")
            return slurm_time(value, "waves.slurm.timeout") if value else None
        value = self.args.slurm_timeout or test.get("slurm_timeout") or scfg.get("timeout")
        return slurm_time(value, f"testcase '{test.get('name')}' slurm_timeout") if value else None

    def plan(self):
        """All jobs for this run: the build job (if any build phase is selected),
        then one test job per selected testcase and seed."""
        model = self.args.model
        selected = self.select_phases()
        build_phases = [p for p in selected if self.phases[p]["scope"] == "build"]
        test_phases = [p for p in selected if self.phases[p]["scope"] == "test"]
        jobs = []

        if test_phases and not self.args.test:
            if self.args.phase and "all" not in split_csv(self.args.phase):
                raise FlowError(f"phase(s) {', '.join(test_phases)} need a test: use -t/--test")
            print(f"dv_flow: note: no -t/--test given, skipping test phase(s): {', '.join(test_phases)}")
            test_phases = []
        if self.args.test and not test_phases:
            print("dv_flow: note: -t/--test ignored, no test-scope phase selected")

        tests = []
        if test_phases:
            all_tests, sections = self.load_tests(model)
            tests = select_tests(all_tests, sections, self.args.test, model)

        build_job = None
        if build_phases:
            # build with coverage when any test being run has coverage on
            build_job = self.make_job("build", model, build_phases, None, 0,
                                      merge_phase_args({}, self.args.phase_args, list(self.phases), build_phases),
                                      coverage=any(t["coverage"] for t in tests))
            jobs.append(build_job)

        for test in tests:
            # a test list cannot change the shared build: drop its build-phase args
            ignored = [k for k in test["phase_args"] if k.split(".")[0] in self.phases
                       and self.phases[k.split(".")[0]]["scope"] == "build"]
            if ignored:
                print(f"dv_flow: warning: '{test['name']}': phase_args for build phase(s) "
                      f"{ignored} ignored (build is shared by all tests; use --phase-args)")
            pa = merge_phase_args({k: v for k, v in test["phase_args"].items() if k not in ignored},
                                  self.args.phase_args, list(self.phases), test_phases)
            dump_waves = bool(self.args.dump_waves or test.get("waves"))   # --dump-waves or `waves: true`
            for seed in seeds_for(test, self.args.seed, self.args.count):
                job = self.make_job("test", model, test_phases, test, seed, pa, coverage=test["coverage"],
                                    dump_waves=dump_waves)
                job.depends_on = build_job
                jobs.append(job)
        if not jobs:
            raise FlowError("nothing to run")
        return jobs

    # -------------------------------------------------------------- wave viewer
    def find_wave_file(self, model, test):
        """The wave file of a testcase: its run with --seed, else the newest run that has one.

        run_dir is rendered with seed '*' and used as a glob pattern, so this
        works with any run_dir layout that contains the seed.
        """
        seed = self.args.seed if self.args.seed is not None else "*"
        _, ctx = self.render_config(model, test=test, seed=seed)
        variables = ctx["variables"]
        wave_file = str(variables.get("wave_file") or DEFAULT_WAVE_FILE)
        run_dir = variables.get("run_dir") or (self.config_dir / f"out/{model}/{test['name']}.{seed}").as_posix()
        pattern = (Path(run_dir) if Path(run_dir).is_absolute() else self.config_dir / run_dir) / wave_file
        found = [Path(p) for p in glob.glob(pattern.as_posix())]
        if not found:
            raise FlowError(f"no wave file {pattern.as_posix()} - run the test with waves first "
                            f"(--dump-waves or `waves: true` in the test list), or give --waves-file")
        return max(found, key=lambda p: p.stat().st_mtime)    # newest run

    def plan_view(self):
        """--open-waves: one 'view' job that opens a wave file in the viewer (DVE by default).

        The wave file comes from --waves-file, or from --testcase (newest run, or --seed).
        It runs in <wave dir>/waves_view/ so it never touches the test's own files.
        """
        model = self.args.model or ""
        if self.args.waves_file:
            wave = Path(self.args.waves_file).resolve()
            if not wave.is_file():
                raise FlowError(f"wave file not found: {wave}")
            test = {**NO_TEST, "name": wave.parent.name, "testname": wave.parent.name}
        else:
            if not model or not self.args.test:
                raise FlowError("--open-waves needs --model and --testcase (one testcase), or --waves-file=PATH")
            all_tests, sections = self.load_tests(model)
            tests = select_tests(all_tests, sections, self.args.test, model)
            if len(tests) != 1:
                raise FlowError(f"--open-waves opens one testcase; '{self.args.test}' selects {len(tests)}: "
                                f"{', '.join(t['name'] for t in tests)}")
            test = tests[0]
            wave = self.find_wave_file(model, test)
        extra = {"wave_path": wave.as_posix(), "wave_dir": wave.parent.as_posix()}

        cfg, ctx = self.render_config(model, test=test, extra=extra)
        wcfg = deep_merge(DEFAULT_WAVES, cfg.get("waves") or {})
        spec = self.model_cfg(cfg, model) if model else {}
        scfg = self.slurm_cfg(cfg, spec, extra=wcfg.get("slurm") or {})
        render = lambda v: self.cfg_tpl.render_string(to_cmdline(v), {**ctx, **extra})  # noqa: E731
        viewer = wcfg.get("viewer") or {}
        cmd = join_args(render(viewer.get("cmd")), render(viewer.get("opts")), render(viewer.get("args")))
        if not cmd:
            raise FlowError("waves.viewer.cmd is empty")
        print(f"dv_flow: opening {wave.as_posix()}")
        return [Job(name=render(wcfg.get("job_name")), kind="view", model=model or "-",
                    directory=wave.parent / "waves_view", phases=["waves"], steps=[Step("waves", "view", cmd)],
                    slurm=scfg, test=test, timeout=self.job_timeout("view", test, scfg),
                    shell=self.job_shell(cfg))]

    # ------------------------------------------------------------------ output
    def jobs_dir(self):
        """Where slurm submission manifests are kept: <out_dir>/jobs."""
        out = Path((self.disc_ctx.get("variables") or {}).get("out_dir") or "out")
        return (out if out.is_absolute() else self.config_dir / out) / "jobs"

    def list(self):
        """--list: phases, models and (with --model) the model's tests."""
        print(f"config: {self.cfg_tpl.path}\n\nphases (execution order):")
        for name, p in self.phases.items():
            print(f"  {name:<12} scope={p['scope']:<6} {p['description']}")
        print("\nmodels:")
        for name, spec in self.models.items():
            if (spec or {}).get("abstract"):
                continue
            resolved = self.model_cfg(self.cfg, name)
            print(f"  {name:<12} phases={','.join(resolved.get('phases') or {})}  "
                  f"testlist={resolved.get('testlist', '-')}")
        if self.args.model:
            self.print_tests(self.args.model)
        return 0

    def runnable_models(self):
        """Model names that can be run (not `abstract: true`), in config order."""
        return [name for name, spec in self.models.items() if not (spec or {}).get("abstract")]

    def print_tests(self, model):
        """Print a model's tests, grouped by section."""
        tests, _ = self.load_tests(model)
        default_limit = self.slurm_cfg(self.cfg, self.model_cfg(self.cfg, model)).get("timeout")
        print(f"\ntests for model '{model}' ({len(tests)} testcase{'' if len(tests) == 1 else 's'}, {self.testlist_path(model).as_posix()}):")
        section = None
        for t in tests:
            if t["section"] != section:
                section = t["section"]
                print(f"  [{section}]")
            print(f"    {t['name']:<20} test={t['testname']:<22} seed={t['seed']!s:<7} "
                  f"count={t['count']:<3} cov={'on' if t['coverage'] else 'off':<4} "
                  f"limit={t.get('slurm_timeout') or default_limit:<5} "
                  f"{t['description']}")

    def list_tests(self):
        """--list-test: tests of --model, or of every runnable model.

        Listing all models keeps going when one model's test list cannot be
        read, and reports the problem for that model.
        """
        if self.args.model:
            self.model_cfg(self.cfg, self.args.model)   # unknown model -> clear error
            self.print_tests(self.args.model)
            return 0
        failed = 0
        for model in self.runnable_models():
            try:
                self.print_tests(model)
            except FlowError as exc:
                print(f"\ntests for model '{model}': cannot list: {exc}")
                failed = 1
        return failed
