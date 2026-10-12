"""
dvflow - the package behind dv_flow.py.

dv_flow.py is only a thin launcher; all the logic lives in these modules.
Read them in this order to understand the flow:

  errors.py      FlowError: the one exception type shown to users
  constants.py   fixed names and defaults (sub-phases, slurm defaults, file names)
  utils.py       small helpers (merging dicts, building command lines, time limits)
  templates.py   reading a YAML file through Jinja2 (includes, variables, merging)
  phase_args.py  test list phase_args + --phase-args -> per-step edits
  testlist.py    loading a test list and selecting testcases / seeds
  jobs.py        Step and Job: what will be run, and where
  flow.py        Flow: loads the config and turns the command line into Jobs
  jobscript.py   writes run.sh / <step>.cmd and builds the sbatch/srun command
  runner.py      runs the Jobs locally or submits them to slurm; prints the summary
  tracker.py     follows submitted slurm jobs until they finish (--wait / --track)
  cli.py         command-line options and main()

Requirements: Python 3.8+, PyYAML >= 5.1, Jinja2 >= 2.11 (see requirements.txt).
"""

__version__ = "1.0.0"
