"""What will be run: Steps grouped into Jobs."""

from dataclasses import dataclass, field
from pathlib import Path

from .constants import RUN_SCRIPT


@dataclass
class Step:
    """One command: a sub-phase (pre_phase / phase / post_phase) of a phase."""
    phase: str
    sub: str
    cmd: str                                            # complete command line
    fail_patterns: list = field(default_factory=list)   # regexes that fail the step when seen in its log

    @property
    def tag(self):
        """'sim.phase' - used for log and .cmd file names."""
        return f"{self.phase}.{self.sub}"

    @property
    def cmd_file(self):
        return f"{self.tag}.cmd"


@dataclass
class Job:
    """A group of steps run in one directory, locally or as one slurm job.

    kind 'build': the build-scope phases (compile, elab), once per run.
    kind 'test' : the test-scope phases (sim) for one testcase and one seed.
    kind 'view' : the wave viewer (--open-waves) for one wave file.
    """
    name: str                     # slurm job name, e.g. dut_a_smoke_test.1.20261011_223055
    kind: str                     # "build" | "test" | "view"
    model: str
    directory: Path               # build_dir or run_dir
    phases: list                  # phase names in this job
    steps: list                   # Step objects, in run order
    slurm: dict                   # merged slurm settings for this job
    test: dict = field(default_factory=dict)
    seed: int = 0
    depends_on: "Job" = None      # test jobs depend on the build job
    timeout: str = None           # slurm --time value
    files: dict = field(default_factory=dict)   # extra files written into the directory: name -> content
    status: str = "PENDING"       # PASS / FAIL ... / SUBMITTED ... / SKIPPED ... / DRY-RUN
    slurm_id: str = None          # set after sbatch

    @property
    def script(self):
        return self.directory / RUN_SCRIPT

    @property
    def label(self):
        """Short name for summaries: 'dut_a:build', 'smoke (seed 1)' or 'waves smoke'."""
        if self.kind == "build":
            return f"{self.model}:build"
        if self.kind == "view":
            return f"waves {self.test['name']}"
        return f"{self.test['name']} (seed {self.seed})"
