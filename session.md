# dv_flow — Session Knowledge Base

A Python design-verification flow at `D:\dv_flow`. It compiles, elaborates and simulates a design (VCS/UVM). It is driven by YAML files with Jinja2 templating and runs jobs locally or on Slurm.

---

## 1. Requirements

| Item | Version |
|---|---|
| Python | 3.8+ (`dv_flow.py` checks the version and the imports at startup) |
| PyYAML | >= 5.1 |
| Jinja2 | >= 2.11 |

Install with `pip install -r requirements.txt`. Everything else comes from the standard library: argparse, subprocess, pathlib, re, shlex, signal, datetime, fnmatch, time, os and sys.

---

## 2. File layout

```
dv_flow.py                  launcher: version + import check, then dvflow.cli.main()
requirements.txt
README.md
session.md                  this file
dvflow/
  __init__.py
  errors.py                 FlowError, YamlSyntaxError
  constants.py              SLURM_MODES, STATUS_FILE, step tags, defaults
  utils.py                  helpers: deep_merge, time parsing, describe_signal, ...
  templates.py              Jinja2 rendering, MergingLoader for {% include %}, comment stripping
  phase_args.py             parsing of -a PHASE[.SUB][.FIELD][+]=VALUE
  testlist.py               test list loading, sections, coverage/waves/timeout merge
  jobs.py                   Job / Step objects
  flow.py                   Flow: plan(), plan_view(), find_wave_file(), slurm_cfg(), list(), list_tests()
  jobscript.py              per-phase scripts: csh_phase_script, sh_phase_script, csh_run_script, sh_run_script,
                            write_script (writes both sets), script_command, slurm_command
  runner.py                 Runner: run_local, run_slurm, execute, summary, write_manifest
  tracker.py                squeue/sacct job tracking (--track, --wait, --poll)
  cli.py                    argparse options and main()
scripts/check_log.py        post-sim log checker -> test_status.yml
example/                    sample project (all paths inside are relative to it; output -> example/out/)
  config.yml                project config (models vcs_base, dut_a, dut_b, dut_c, demo, demo2)
  common/master_config.yml  shared variables, phases, slurm, waves, anchors
  common/log_check.yml      keyword/profile rules for check_log.py
  testlists/common_tests.yml, dut_a_tests.yml, dut_b_tests.yml, demo_tests.yml
doc/dv_flow_user_guide.docx comprehensive user guide
doc/dv_flow_training.pptx   32-slide training deck
```

Doc generators live in the session scratchpad under `docbuild/`: guide.txt, build_docx.js (docx-js), build_pptx.js (pptxgenjs) and fitcheck.py. They are **not** copied into the repo. Build them from `docbuild/` with `node build_docx.js` and `NODE_PATH=$PWD/node_modules APPLY_THEME=<pptx skill>/scripts/apply_theme.js node build_pptx.js`. Validate with `PYTHONPATH=$PWD/pylib python <skill>/scripts/office/validate.py <file>` and `python fitcheck.py <pptx>`.

---

## 3. Configuration model

### Templating
- Every YAML file is rendered by Jinja2 (StrictUndefined) before YAML parsing, once per job.
- Comment lines are stripped before rendering, so Jinja syntax inside `#` comments is harmless.
- `variables:` are resolved iteratively, so one variable can reference another. `-D KEY=VALUE` beats `variables:`.
- Context: `env`, `config_dir`, `cwd`, `user`, `date`, `time` (YYYYmmdd_HHMMSS, the same for the whole run), `model`, `test.*`, `seed`, `gui`, `coverage`, `dump_waves`, `mode`, `python` and `flow_dir`.

### Hierarchy (master and sub files)
- `{% include 'common/master_config.yml' %}` pastes the master in as text, so YAML anchors carry across files.
- The MergingLoader deep-merges repeated top-level keys and the including file wins.
- Include search path: config dir, then `-I DIR`, then `$DV_FLOW_INCLUDE`.
- An include loop is reported as "include loop". YAML errors are reported as YamlSyntaxError, not as misleading Jinja errors.
- Test lists use the same include mechanism (common_tests.yml is included by the model lists).

### Reuse
- YAML anchors in the `anchors:` key (ignored by the flow): `&vcs_compile`, `&vcs_elab`, `&vcs_sim`, `&vcs_sim_main`, `&demo_compile`, `&demo_elab`. Use them with `*name` or `<<: *name`.
- Models support `extends: <model>`, and `abstract: true` marks a base-only model (vcs_base).

### Phases
- User-defined, executed in the order listed under `phases:`.
- `scope: build` runs once per invocation in `build_dir`. `scope: test` runs once per testcase and seed in `run_dir`.
- `test_args`: for test scope, added to the main command (sim: `+UVM_TESTNAME={{ test.testname }}`).
- `fail_patterns`: case-insensitive regexes checked in each step log. Compile has `["error-"]`.
- `files:` are files written into the job dir before the steps run (e.g. waves.tcl).

### Steps per phase
`pre_phase`, `phase` (main) and `post_phase`. Each has `cmd`, `opts` and `args`; opts and args are optional, and each can be a string or a list. The final command is `cmd opts args <test_args> <phase_args>`.

### Key variables (master_config.yml)
proj_root, out_dir, build_dir, run_dir, **simv_dir** and **simv_name** (the programmable simv location), debug_tool (**dve** by default; verdi adds -kdb), wave_file (waves.vpd), wave_scope, wave_depth, tb_top, verbosity, uvm_max_quit_count (**10**), cov_types, cov_dir, log_rules, log_profile and check_log.

---

## 4. Test lists

- One test list per model, divided into sections.
- Entry fields: `testcase` (the name the user types), `testname` (the UVM test → `+UVM_TESTNAME`), `description`, `phase_args`, `seed`, `count` and `coverage`. Optional fields are `waves`, `slurm_timeout`/`slurm-timeout`, `slurm_mem`, `max_quit_count`, `verbosity`, `timeout` and `log_profile`. Any extra field is available as `test.<field>`.
- Several testcases can map to the same `testname` with different args.
- `coverage` can be set on the section or on the test; the test wins.
- Hyphenated keys are normalized to underscores before the section and test merge.
- `--testcase` accepts comma lists, section names, globs (`rand_*`) and `all`.

---

## 5. Command line (cli.py)

| Option | Purpose |
|---|---|
| `-c/--config` | config file (default `$DV_FLOW_CONFIG`, else `./config.yml`, else `example/config.yml`) |
| `-m/--model` | model |
| `-p/--phase` | comma list or `all` (default) |
| `-t/--test/--testcase` | testcases, sections, globs or `all` |
| `-a/--phase-args` | `PHASE[.SUB][.FIELD][+]=VALUE` (repeatable) |
| `--seed`, `--count` | seed (int or `random`) and run count overrides |
| `--slurm-timeout`, `--slurm-mem`, `--slurm-opt` | per-run Slurm overrides (`--slurm-opt` is repeatable) |
| `--dump-waves`, `--open-waves`, `--waves-file` | waves |
| `-D KEY=VALUE`, `-I DIR` | Jinja variables, include directories |
| `--slurm` / `--local`, `--mode batch\|interactive`, `--gui` | where and how jobs run |
| `-n/--dry-run`, `-q/--quiet` | print only; suppress tool output |
| `-l/--list`, `--list-test/--list_test` | list models and phases; list tests (one model or all) |
| `--wait`, `--track [MANIFEST]`, `--poll SEC` | Slurm tracking |

### Phase-arg syntax
- `sim=X`: extra args at the end of the main command; **replaces** the test list's phase_args.
- `sim+=X`: **appends** to them.
- `sim.pre.opts=X` / `sim.post.args+=X` / `compile.main.cmd=X`: replace or append a step's cmd, opts or args. SUB is pre, main or post (default main).
- `PHASE.SUB.cmd=` with an empty value skips that step.
- Command-line args beat test-list args.

### Exit codes
0 all passed · 1 a job failed · 2 user error (FlowError) · 3 jobs still running (track) · 130 Ctrl-C.

### Examples
```
python dv_flow.py --list
python dv_flow.py --model=dut_a --list-test
python dv_flow.py --model=dut_a --phase=compile,elab
python dv_flow.py --model=dut_a --testcase=smoke,rand_read -a "sim+=+UVM_VERBOSITY=UVM_HIGH"
python dv_flow.py --model=dut_a --testcase=regression --slurm --wait       # --wait implies batch
python dv_flow.py --model=dut_a --phase=sim --testcase=smoke --slurm --gui   # interactive (default)
python dv_flow.py --track --wait
python dv_flow.py --model=dut_a --testcase=smoke --dump-waves
python dv_flow.py --model=dut_a --testcase=smoke --open-waves --slurm-mem=32G
```

---

## 6. Execution and failure handling

- **Job scripts:** per phase `<phase>.csh` (tcsh) and `<phase>.sh` (sh), each holding the phase's pre_phase, phase and post_phase commands. `run.csh` and `run.sh` run the phases in order. Both sets are always written. `job_shell: tcsh|csh|sh` (master config, default tcsh) or `--shell` picks which set dv_flow and Slurm run. There are no per-step `.cmd` files any more.
- Step runner in a phase script: tcsh uses `( cmd ; echo $status > X.rc ) |& tee X.log`; sh uses a multi-line `( cmd \n echo $? > X.rc ) 2>&1 | tee X.log`. Every step writes `<phase>.<sub>.log`.
- Local runs execute `run.csh` / `run.sh` themselves. When that shell is missing (Windows has no tcsh), Runner.run_steps runs the steps from Python with `shell=True`.
- Steps run in order. **The first failing step stops the job**: the remaining steps are skipped and `status` gets `FAIL <step> <why>`.
- A step fails on a non-zero rc or on a `fail_patterns` match. Compile fails on `error-` in its log.
- UVM_ERROR and UVM_FATAL do **not** stop the sim phase itself. `+UVM_MAX_QUIT_COUNT=10,NO` is the default, and the pass/fail verdict comes from `check_log.py` in post_phase.
- Test jobs depend on the build job and are skipped if the build failed.
- run.sh traps TERM and INT and writes the status file. run.csh only has `onintr` (SIGINT), because csh cannot trap SIGTERM, so the tracker relies on sacct for cancelled or timed-out jobs. Both map rc > 128 to a signal name.
- **csh gotchas (all fixed in the generator):**
  - A here-document's end marker must be written with its quotes (`'DV_FLOW_EOF'`).
  - A one-line `if (..) echo > file` truncates the file even when the condition is false, so use if/then/endif.
  - bsd-csh reports a signal exit as a negative `$status`, so add 256.
  - `nonomatch` is needed so `[pre]` and similar patterns are passed on unchanged.
  - csh expands `{a,b}`.
- csh/tcsh/sh tests run in WSL Ubuntu-22.04 (tcsh, bsd-csh, dash). Jinja2 is installed into `scratchpad/wslpy`, and the test script is `scratchpad/csh/test_csh.sh`.

### check_log.py
Profiles and keywords are configured in `common/log_check.yml`. It greps the sim log and writes `test_status.yml`. **All script output is YAML** (manifests, status and check results); there is no JSON.

---

## 7. Slurm

- **Default mode is interactive** (`slurm.mode: interactive` in constants.py DEFAULT_SLURM and master_config.yml). `--mode=batch` selects sbatch, and `--wait` without `--mode` implies batch (not for the `--open-waves` viewer).
- **sbatch (batch)**: submits and returns at once, so jobs run in parallel. The flow uses `--parsable` to get the job id. Test jobs use `--dependency=afterok:<build id>`. Output is a manifest at `<out_dir>/jobs/<model>_<time>.yml`.
- **srun (interactive)**: blocking and attached to the terminal (`--pty`). Use it for debug and GUI work. It produces no manifest.
- `--gui` adds `gui_options` (`--x11`) and makes the sim run with `-gui=<debug_tool>` (DVE).
- Job name: `{{ test.testname }}.{{ seed }}.{{ time }}`. Build job name: `{{ model }}.build.{{ time }}`.
- Timeout: `slurm.timeout` defaults to **1h**, and the build job uses `build_timeout` (2h). Priority: `--slurm-timeout`, then testcase `slurm_timeout`/`slurm-timeout`, then model or master. Formats include 1h, 90m, 2h30m, 1d12h, 45, 01:30:00, 1-00:00:00 and unlimited. Slurm itself kills a job at its limit (`--time`).
- Memory priority: `--slurm-mem`, then testcase `slurm_mem`, then `waves.slurm.mem` (viewer only), then model `slurm.mem`, then master `slurm.mem` (8G).
- `slurm.options` is added to every job, and `--slurm-opt` adds more per run.
- Tracking (tracker.py) uses squeue for live jobs and sacct for finished ones (state and exit code). It detects CANCELLED, TIMEOUT, OUT_OF_MEMORY, NODE_FAIL and signal kills. `--wait` polls every `slurm.poll_interval` seconds (default 30) or `--poll`.

---

## 8. Waves (VPD + DVE)

- A test with `waves: true`, or a run with `--dump-waves`, sets `dump_waves`. The vcs_sim anchor then writes `waves.tcl` (UCLI: `dump -file waves.vpd -type VPD`, `dump -add <wave_scope> -depth <wave_depth> -aggregates`, `run`, `quit`) and adds `-ucli -do waves.tcl`.
- `--open-waves --testcase=X [--seed=N]` finds `<run_dir>/<wave_file>` in the newest run, or uses `--waves-file PATH`. It runs `dve -full64 -vpd <wave_path>` in `<wave_dir>/waves_view/`.
- Viewer Slurm settings come from `waves.slurm`: enabled, interactive, gui, 4h, 16G and `--cpus-per-task=2`. They are merged over `slurm:`, and `--local` opens the viewer on the current machine.
- Test list example: in dut_a, `reg_reset` has `waves: true` and `slurm_mem: 16G`.

---

## 9. Verification status

- **Tested:** dry runs, local echo runs (demo models), refactor equivalence (dry-run diffs before and after), a fake Slurm (sbatch, squeue and sacct stubs), kill detection with manual signals, and the waves dry run.
- **Not tested:** real VCS, the real UCLI dump, real DVE and real srun/sbatch on a cluster.
- Docs passed validation and fitcheck. No visual render was possible because LibreOffice is not installed.

---

## 10. Known gotchas and fixes (history)

| Issue | Fix |
|---|---|
| Jinja syntax in YAML comments broke rendering | comment lines stripped before rendering |
| Include recursion caused a RecursionError | reported as "include loop" |
| YAML syntax errors were masked by Jinja errors | YamlSyntaxError |
| `%` in the argparse help crashed `-h` | escaped as `%%` |
| `slurm-timeout` lost to the section's `slurm_timeout` | hyphens normalized to underscores before the merge |
| README read with the wrong encoding | read as utf-8 |
| Heredoc patches corrupted escape sequences | edit with the Write/Edit tools |
| EBUSY writing the pptx | close PowerPoint before rebuilding |

---

## 11. Documentation upkeep

Keep `doc/dv_flow_user_guide.docx` and `doc/dv_flow_training.pptx` in sync with every feature change. The guide includes chapters on srun vs sbatch, Waves, and "Memory and extra Slurm options". The deck has 32 slides, including "Waves in DVE on Slurm".

## 12. Open ideas (offered, not requested)

- Copy the doc generator scripts into `doc/`.
- A sim watchdog, or `--cancel` to scancel the jobs of a manifest.
- Verdi FSDB wave support.
