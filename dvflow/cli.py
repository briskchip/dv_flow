"""Command-line options and main()."""

import argparse
import os
import sys
from pathlib import Path

from .constants import JOB_SHELLS, SLURM_MODES
from .errors import FlowError
from .flow import Flow
from .runner import Runner, latest_manifest
from .tracker import track_jobs

EXAMPLES = """
examples:
  # list models / phases / tests
  python dv_flow.py --list
  python dv_flow.py --list-test                  # tests of every model
  python dv_flow.py --model=dut_a --list-test    # tests of one model

  # compile + elab only
  python dv_flow.py --model=dut_a --phase=compile,elab

  # run two testcases, add sim args from the command line
  python dv_flow.py --model=dut_a --testcase=smoke,rand_read -a "sim+=+UVM_VERBOSITY=UVM_HIGH"

  # slurm: interactive (default, srun) with a GUI, or batch (sbatch) and wait for results
  python dv_flow.py --model=dut_a --phase=sim --testcase=smoke --slurm --gui
  python dv_flow.py --model=dut_a --testcase=regression --slurm --mode=batch --wait

  # status of the latest slurm submission
  python dv_flow.py --track

  # dump waves, then open them in DVE on slurm (with more memory)
  python dv_flow.py --model=dut_a --testcase=smoke --dump-waves
  python dv_flow.py --model=dut_a --testcase=smoke --open-waves --slurm-mem=32G
"""


def default_config():
    """$DV_FLOW_CONFIG, else ./config.yml, else the sample config shipped in example/."""
    if os.environ.get("DV_FLOW_CONFIG"):
        return os.environ["DV_FLOW_CONFIG"]
    if os.path.exists("config.yml"):
        return "config.yml"
    sample = Path(__file__).resolve().parent.parent / "example" / "config.yml"
    return sample.as_posix() if sample.exists() else "config.yml"


def build_parser():
    p = argparse.ArgumentParser(
        prog="dv_flow",
        description="YAML/Jinja2 driven DV flow: compile, elaborate and simulate.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EXAMPLES,
    )
    # what to run
    p.add_argument("-c", "--config", default=default_config(),
                   help="flow config YAML (default: $DV_FLOW_CONFIG, else ./config.yml, "
                        "else the sample example/config.yml)")
    p.add_argument("-m", "--model", help="model to run")
    p.add_argument("-p", "--phase", default="all",
                   help="comma separated phases to run, or 'all' (default: all)")
    p.add_argument("-t", "--test", "--testcase", dest="test",
                   help="comma separated testcase names, section names, globs (rand_*), or 'all'")
    # how to run it
    p.add_argument("-a", "--phase-args", action="append", metavar="PHASE[.SUB][.FIELD][+]=VALUE",
                   help="PHASE[.SUB]=X: extra args at the end of the command (replaces the test list "
                        "phase_args; += appends to them). PHASE[.SUB].FIELD=X: replace that sub-phase's "
                        "cmd|opts|args (+= appends; '.cmd=' with no value skips the step). "
                        "SUB is pre|main|post (default main). Repeatable.")
    p.add_argument("--seed", help="seed override (integer or 'random')")
    p.add_argument("--count", type=int, help="run count override")
    p.add_argument("--slurm-timeout", metavar="TIME",
                   help="slurm time limit for every test job, e.g. 30m, 2h, 01:30:00 "
                        "(overrides testcase slurm_timeout and slurm.timeout)")
    p.add_argument("--slurm-mem", metavar="SIZE",
                   help="slurm memory (--mem) for the jobs of this run, e.g. 16G "
                        "(overrides testcase slurm_mem and slurm.mem)")
    p.add_argument("--slurm-opt", action="append", metavar="OPTION",
                   help="extra sbatch/srun option for the jobs of this run, e.g. --slurm-opt=--cpus-per-task=8 "
                        "or --slurm-opt=--qos=high. Repeatable.")
    # waves
    p.add_argument("--dump-waves", action="store_true",
                   help="dump waves (VPD) in the selected tests (same as `waves: true` in the test list)")
    p.add_argument("--open-waves", action="store_true",
                   help="open the waves of --testcase (newest run, or --seed) in the viewer (DVE), on slurm "
                        "by default; settings in the config's `waves:` section")
    p.add_argument("--waves-file", metavar="PATH",
                   help="with --open-waves: open this wave file instead of looking it up by testcase")
    p.add_argument("-D", "--define", action="append", metavar="KEY=VALUE",
                   help="Jinja variable for config/test list (overrides 'variables'). Repeatable.")
    p.add_argument("-I", "--include-dir", action="append", metavar="DIR",
                   help="extra directory searched by {%% include %%} (also $DV_FLOW_INCLUDE). Repeatable.")
    # where to run it
    g = p.add_mutually_exclusive_group()
    g.add_argument("--slurm", dest="slurm", action="store_true", default=None, help="run jobs through slurm")
    g.add_argument("--local", dest="slurm", action="store_false", help="run jobs locally (ignore slurm.enable)")
    p.add_argument("--mode", choices=SLURM_MODES, help="slurm mode (overrides slurm.mode; default interactive, --wait implies batch)")
    p.add_argument("--gui", action="store_true", help="GUI job (adds slurm gui_options, sets {{ gui }})")
    p.add_argument("--shell", choices=JOB_SHELLS,
                   help="job scripts to run: tcsh/csh -> run.csh + <phase>.csh, sh -> run.sh + <phase>.sh "
                        "(both sets are always written; default: job_shell in the config, else tcsh)")
    # output / inspection
    p.add_argument("-n", "--dry-run", action="store_true", help="print commands, run nothing")
    p.add_argument("-q", "--quiet", action="store_true", help="don't echo tool output (logs still written)")
    p.add_argument("-l", "--list", action="store_true", help="list phases, models (and tests with -m)")
    p.add_argument("--list-test", "--list_test", dest="list_test", action="store_true",
                   help="list the tests of --model, or of every model when --model is not given")
    # slurm tracking
    p.add_argument("--wait", action="store_true",
                   help="slurm batch: wait until all submitted jobs finish, then print pass/fail "
                        "(implies --mode=batch)")
    p.add_argument("--track", nargs="?", const="latest", metavar="MANIFEST",
                   help="show the status of submitted slurm jobs (default: latest manifest); "
                        "with --wait, wait for them to finish")
    p.add_argument("--poll", type=int, metavar="SEC", help="--wait polling interval (default: slurm.poll_interval)")
    return p


def main(argv=None):
    """Entry point. Exit codes: 0 ok, 1 a job failed, 2 user error, 3 jobs still running, 130 Ctrl-C."""
    args = build_parser().parse_args(argv)
    try:
        if args.track and args.track != "latest":        # a manifest file: no config needed
            return track_jobs(args.track, wait=args.wait, poll=args.poll)
        flow = Flow(args)
        if args.track:
            return track_jobs(latest_manifest(flow), wait=args.wait, poll=args.poll)
        if args.list_test:
            return flow.list_tests()
        if args.open_waves:
            return Runner(flow).execute(flow.plan_view())
        if args.list:
            return flow.list()
        if not args.model:
            raise FlowError("-m/--model is required (use --list to see models)")
        return Runner(flow).execute(flow.plan())
    except FlowError as exc:
        print(f"dv_flow: error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ndv_flow: interrupted", file=sys.stderr)
        return 130
