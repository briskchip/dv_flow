# dv_flow

YAML + Jinja2 driven design-verification flow: compile, elaborate, simulate — locally or on Slurm.

## Requirements

- Python **3.8** or newer
- `PyYAML >= 5.1` and `Jinja2 >= 2.11`: `pip install -r requirements.txt`
- everything else is the Python standard library
- bash to run the generated job scripts; Slurm client commands for cluster runs

Full documentation: `doc/dv_flow_user_guide.docx`, training deck: `doc/dv_flow_training.pptx`.

## Files

| File | Purpose |
|---|---|
| `dv_flow.py` | the command you run (checks Python / packages, then starts `dvflow`) |
| `dvflow/` | the code, one module per job: `flow.py` (config to jobs), `runner.py` (run / submit), `jobscript.py` (run.sh, .cmd, sbatch), `tracker.py` (slurm status), `templates.py` (Jinja + YAML), `testlist.py`, `phase_args.py`, `jobs.py`, `utils.py`, `constants.py`, `errors.py`, `cli.py` |
| `scripts/check_log.py` | post-simulation PASS/FAIL check, writes `test_status.yml` |
| `requirements.txt` | Python packages |
| `example/` | sample project: copy it to start your own (paths below are relative to it) |
| `example/config.yml` | project config: includes the master, defines the models |
| `example/testlists/*.yml` | per-model test lists (`sections` → test entries) |
| `example/common/master_config.yml` | master config: shared `variables`, `phases`, `slurm`, `anchors` |
| `example/common/log_check.yml` | pass/fail rules for `check_log.py` |
| `example/testlists/common_tests.yml` | master test list: shared `variables`, section-default anchors, field reference |

Config choice: `-c FILE`, else `$DV_FLOW_CONFIG`, else `./config.yml`, else `example/config.yml`.
Paths in a config (test lists, `common/...`, `out/`) are relative to the config's directory, so the
sample's output goes to `example/out/`. For a real project, copy `example/` to your project and run
with `-c <project>/config.yml` (or set `DV_FLOW_CONFIG`).

## Hierarchy (master + sub files)

A config or test list includes a master file with `{% include '...' %}` as its first line. Jinja
pastes the master in as text, so the master's YAML anchors can be used with `*name` / `<<: *name`.
Top-level keys present in both (`variables:`, `phases:`, `slurm:`, `models:`, `sections:` ...) are
deep-merged and the including file wins, so a sub file only lists what it adds or changes.
Masters can include other masters. Include paths are searched in the file's own directory, `-I DIR`,
`$DV_FLOW_INCLUDE` (path-separated), then parent directories, then cwd.

## Concepts

- **Phases** (`phases:` in config) – user defined, run in declared order.
  `scope: build` phases run once per invocation (`build_dir`); `scope: test` phases
  run once per test + seed (`run_dir`).
- **Models** – each defines its phases; each phase has `pre_phase`, `phase`,
  `post_phase`, each with `cmd`, `opts`, `args` (string or list).
  Command line = `cmd opts args <phase args>`. Empty `cmd` = skipped.
  `extends:` inherits another model, `abstract: true` hides a base model.
  Each model has a `testlist:` (path relative to config).
- **Test list** entries: `testcase`, `testname`, `description`, `phase_args`, `seed`, `count`.
- **Testcase options** – many testcases can share one `testname` (UVM test), each with its own
  `phase_args`, `seed`, `count` and any extra fields (e.g. `verbosity`, `timeout`), which
  config.yml can use as `{{ test.<field> }}`.
- **Comments** – full-line `#` comments are removed before Jinja runs, so `{{ }}` / `{% %}` inside them are ignored.
- **Per-field edits** – add `cmd`, `opts` or `args` to a phase args key to change that field of a
  sub-phase instead of adding extra args. `=` replaces, `+=` appends; the test list is applied first,
  then the command line, so the command line wins:
  ```bash
  -a "sim.opts+=+UVM_OBJECTION_TRACE"   # append to sim opts
  -a "sim.opts=+ntb_random_seed=7"      # replace sim opts
  -a "compile.args=-f my_files.f"       # replace compile args
  -a "elab.pre.cmd=echo hello"          # replace (or create) the elab pre_phase cmd
  -a "sim.post.cmd="                    # empty cmd -> skip the sim post_phase step
  ```
  In a test list use the same keys, with a trailing `+` to append: `"sim.opts+": "+foo"`.
- **Coverage** – `coverage: true/false` on a test, or on a section (a section can be a mapping with
  defaults for its tests plus `tests:`; the test's own value wins). Coverage tests run sim with the
  `-cm ...` options from config.yml, and the shared build is compiled with coverage when any test being
  run needs it. Force it for a whole run with `-D coverage=true` / `-D coverage=false`.
- **Phase args** – `phase_args: {sim: "...", "sim.pre": "...", "sim.post": "..."}` in the test list;
  `-a/--phase-args` on the command line **overrides** them (`-a "sim+=..."` appends instead).
- **Jinja** – config and test lists are rendered before YAML parsing. Context: `env`, `model`,
  `test.*`, `seed`, `time`, `gui`, `mode`, `variables`, and `-D KEY=VALUE`.
  The config is rendered per job, so `{{ seed }}` / `{{ test.testname }}` work anywhere in it.
- **Slurm** – `enable`, `mode: batch|interactive`, `gui`, `options`, `gui_options`, `partition`.
  Job name defaults to `testname.seed.time`. In batch mode, test jobs depend on the build job
  (`--dependency=afterok`). Every job writes a reproducible `run.sh` into its directory, plus one `<phase>.<sub>.cmd` file per step (the exact command; `run.sh` executes these) and a `<phase>.<sub>.log` per step.
  Steps run in order and the first failing step stops the job (later steps are skipped); if the build job fails, test jobs are skipped.
- **Log checks** – a phase's `fail_patterns` (compile: `error-`, case-insensitive) are searched in each
  step's log; a match fails the step even when the tool exits 0, and the next steps don't run.
- **UVM quit count** – sim runs with `+UVM_MAX_QUIT_COUNT=10,NO` by default. Change it globally with
  `-D uvm_max_quit_count=N`, or per testcase with `max_quit_count: N` in the test list.

## Pass/fail checking

The sim `post_phase` runs `scripts/check_log.py` on the sim log. Rules are Python regexes in
`common/log_check.yml`, grouped in profiles (`uvm`, `uvm_verdict`, `uvm_expect_errors`, `demo`):
`fail` (any match fails), `require` (each must appear, e.g. the UVM report summary) and `ignore` (waivers).
Pick a profile per testcase with `log_profile:` in the test list, or add one-off patterns:
`--phase-args="sim.post.opts+=--require 'TEST PASSED' --ignore 'known_bug_42'"`.
The result is written to `test_status.yml` in the run directory; the script exits 0 (PASS) / 1 (FAIL).

## Tracking slurm jobs

Every job writes `dv_flow.status` in its directory (`RUNNING`, then `PASS` or `FAIL <step> ...`), so the
result does not depend on slurm accounting. A batch submission saves a manifest in `<out_dir>/jobs/`.

```bash
python dv_flow.py -m dut_a -t regression --slurm --wait          # submit, wait, print pass/fail
python dv_flow.py --track                                        # status of the latest submission
python dv_flow.py --track out/jobs/dut_a_<time>.yml --wait --poll 60
```
Each poll makes one `squeue` call for all jobs (500 ids per call) and one `sacct` call for jobs that
left the queue, then reads the status files. Interval: `slurm.poll_interval` (default 30 s) or `--poll`.
Progress is printed only when the counts change. Test jobs are submitted with
`--kill-on-invalid-dep=yes`, so they are cancelled if the build job fails.
Exit code: 0 all passed, 1 any failed, 3 still running (`--track` without `--wait`).

Time limit: every test job is submitted with `--time` from `slurm.timeout` (default `1h`); the build
job uses `slurm.build_timeout`. Override per testcase (or per section) with `slurm_timeout: 2h` in the
test list, or for a whole run with `--slurm-timeout=30m`. Formats: `1h`, `90m`, `2h30m`, `1d12h`,
`45` (minutes), `01:30:00`, `1-00:00:00`, `unlimited`. A job that hits the limit is reported as
`killed by SIGTERM` / `FAIL (slurm TIMEOUT)`.

Killed jobs: `run.sh` traps SIGTERM (what slurm sends on `scancel`, time limit or preemption) and writes
`FAIL <step> killed by SIGTERM ...`; a step killed with a signal is recorded as `rc=137 (signal KILL)`.
If the whole job is SIGKILLed (node lost, KillWait expired) the status stays `RUNNING`, and the tracker
reports slurm's own state from `sacct` (`CANCELLED`, `TIMEOUT`, `OUT_OF_MEMORY`, `NODE_FAIL`, `PREEMPTED`),
or `FAIL (job died)` if the job is gone from both `squeue` and `sacct`.

## Waves

```bash
python dv_flow.py -m dut_a -t smoke --dump-waves          # or `waves: true` on the testcase
python dv_flow.py -m dut_a -t smoke --open-waves          # DVE on slurm (srun --pty --x11)
python dv_flow.py -m dut_a -t smoke --open-waves --seed=1 --slurm-mem=48G --slurm-timeout=8h
python dv_flow.py --open-waves --waves-file=out/dut_a/tests/smoke.1/waves.vpd --local
```
The sim writes `waves.tcl` (UCLI dump script) into the run directory and runs `simv -ucli -do waves.tcl`,
which dumps `<run_dir>/waves.vpd` (`wave_file`, `wave_scope`, `wave_depth` variables). `--open-waves` opens
the newest run's file (or `--seed`'s) with the viewer in the config's `waves:` section; its `waves.slurm`
block (default: interactive, X11, 4h, 16G) is merged over `slurm:` for the viewer job only.

Slurm memory: `slurm.mem` (master/model), `slurm_mem:` per testcase, `--slurm-mem=` per run.
Any other sbatch/srun option for one run: `--slurm-opt=--qos=high` (repeatable).

## Usage

```bash
python dv_flow.py --list                     # phases + models
python dv_flow.py -m dut_a --list            # + tests
python dv_flow.py --list-test                # tests of every model
python dv_flow.py -m dut_a --list-test       # tests of one model

python dv_flow.py -m demo -t all             # runnable demo (echo only)

python dv_flow.py -m dut_a -p compile,elab                     # build only
python dv_flow.py -m dut_a -p all -t smoke,rand_read \
       -a "sim=+UVM_VERBOSITY=UVM_HIGH" -a "compile=+define+FAST"
python dv_flow.py -m dut_a -t regression --count 10 --slurm --mode batch
python dv_flow.py -m dut_a -p sim --testcase=smoke --seed 42 --slurm --mode interactive --gui
python dv_flow.py -m dut_a -t 'rand_*' -D cov=true -n           # dry run
```

`-t` / `--testcase` accepts testcase names, section names, globs, or `all`.
`-p` accepts a comma list or `all` (default). Exit code is non-zero if any job fails.
