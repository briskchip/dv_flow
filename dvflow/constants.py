"""Fixed names and default values used across the flow."""

# Every phase has up to three steps, run in this order.
SUBPHASES = ("pre_phase", "phase", "post_phase")
# The only keys a step may have. The command line of a step is "cmd opts args ...".
SUBPHASE_KEYS = ("cmd", "opts", "args")
# Short names accepted in phase-args keys: "sim.pre", "sim.main", "sim.post".
SUBPHASE_ALIASES = {"pre": "pre_phase", "main": "phase", "post": "post_phase"}

# scope: build -> one build job per run; scope: test -> one job per testcase and seed.
PHASE_SCOPES = ("build", "test")
SLURM_MODES = ("batch", "interactive")

# How many times the `variables:` section is re-rendered so that variables can
# refer to each other in any order (see templates.YamlTemplate.render).
MAX_VARIABLE_PASSES = 10

# Test context used when there is no real test: while discovering models and
# phases, and for build jobs (which are shared by all tests).
NO_TEST = {"name": "build", "testname": "build", "description": "", "section": "", "phase_args": {}}

# Slurm settings used when the config does not set them. The master config's
# `slurm:` section and a model's own `slurm:` block are deep-merged on top.
DEFAULT_SLURM = {
    "enable": False,
    "mode": "interactive",                                # srun --pty; batch = sbatch (--mode=batch, --wait)
    "gui": False,
    "partition": None,
    "options": [],                                        # added to every job
    "gui_options": ["--x11"],                             # added to GUI jobs
    "batch": {"cmd": "sbatch", "options": ["--parsable"]},
    "interactive": {"cmd": "srun", "options": ["--pty"]},
    "job_name": "{{ test.testname }}.{{ seed }}.{{ time }}",
    "build_job_name": "{{ model }}.build.{{ time }}",
    "query": {"squeue": "squeue", "sacct": "sacct"},       # commands used by the tracker
    "poll_interval": 30,                                  # seconds between tracker polls
    "timeout": "1h",                                      # --time for test jobs
    "build_timeout": None,                                # --time for the build job (default: timeout)
    "mem": None,                                          # --mem, e.g. "8G" (per testcase: slurm_mem)
}

# Wave viewer (--open-waves). The config's `waves:` section is deep-merged on top.
# viewer cmd / opts / args are rendered with {{ wave_path }} (the VPD file) and
# {{ wave_dir }} (its directory). `slurm` is merged over the normal slurm
# settings for the viewer job only.
DEFAULT_WAVES = {
    "viewer": {"cmd": "dve", "opts": "-full64", "args": "-vpd {{ wave_path }}"},
    "job_name": "waves.{{ test.name }}.{{ time }}",
    "slurm": {"enable": True, "mode": "interactive", "gui": True, "timeout": "4h", "mem": "16G"},
}
# Name of the wave file when the config does not set the `wave_file` variable.
DEFAULT_WAVE_FILE = "waves.vpd"

# Files written into every job directory.
# Every job directory gets two sets of scripts with the same steps:
#   <phase>.csh + run.csh   (tcsh / csh)      <phase>.sh + run.sh   (POSIX sh)
# `job_shell` (config) or --shell picks the set that dv_flow and slurm run.
JOB_SHELLS = ("tcsh", "csh", "sh")
DEFAULT_JOB_SHELL = "tcsh"
STATUS_FILE = "dv_flow.status"        # RUNNING, then PASS or FAIL <step> <reason>
TEST_STATUS_FILE = "test_status.yml"  # written by scripts/check_log.py

# Slurm job states that mean "not finished yet".
SLURM_ACTIVE = {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "REQUEUED", "RESIZING",
                "SUSPENDED", "STAGE_OUT", "SIGNALING"}
# Job ids per squeue / sacct call, so a huge regression never exceeds the command-line length.
QUERY_CHUNK = 500
