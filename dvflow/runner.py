"""Running the planned Jobs: locally, or by submitting them to slurm."""

import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from .constants import STATUS_FILE
from .errors import FlowError
from .jobscript import script_command, slurm_command, write_script
from .tracker import track_jobs
from .utils import describe_signal


class Runner:
    def __init__(self, flow):
        self.flow = flow
        self.args = flow.args
        self.warned = set()

    # ------------------------------------------------------------------ local
    def run_local(self, job):
        """Run the job's run.csh / run.sh, the same script slurm runs.

        When that shell is not installed (e.g. tcsh on Windows), the steps are run
        one by one from Python with the system shell instead.
        """
        cmd = script_command(job, job.script.name)
        if not shutil.which(cmd[0]):
            if cmd[0] not in self.warned:
                print(f"  dv_flow: note: {cmd[0]} not found, running the steps with the system shell")
                self.warned.add(cmd[0])
            return self.run_steps(job)
        print(f"  -> {' '.join(cmd)}")
        proc = subprocess.Popen(cmd, cwd=job.directory, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace")
        for line in proc.stdout:
            if not self.args.quiet:
                sys.stdout.write("     " + line)
        rc = proc.wait()
        try:
            status = (job.directory / STATUS_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            status = ""
        if rc == 0 and status == "PASS":
            job.status = "PASS"
            return True
        # "FAIL compile.phase rc=2" -> "FAIL (compile.phase rc=2, log: <dir>/compile.phase.log)"
        detail = status[5:] if status.startswith("FAIL ") else f"rc={rc}{describe_signal(rc)}"
        log = job.directory / f"{detail.split()[0]}.log"
        job.status = f"FAIL ({detail}" + (f", log: {log.as_posix()})" if log.exists() else ")")
        print(f"  !! failed: {detail}")
        return False

    def run_steps(self, job):
        """Run the steps of a job one by one from Python; stop at the first failure."""
        for i, st in enumerate(job.steps):
            print(f"  -> [{st.tag}] {st.cmd}")
            log = job.directory / f"{st.tag}.log"
            fail_re = re.compile("|".join(f"(?:{x})" for x in st.fail_patterns), re.I) if st.fail_patterns else None
            hit = None
            with open(log, "w", encoding="utf-8") as fh:
                proc = subprocess.Popen(st.cmd, shell=True, cwd=job.directory, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, errors="replace")
                for n, line in enumerate(proc.stdout, start=1):
                    fh.write(line)
                    if fail_re and hit is None and fail_re.search(line):
                        hit = f"{n}:{line.strip()}"     # first match; the tool still runs to the end
                    if not self.args.quiet:
                        sys.stdout.write("     " + line)
                rc = proc.wait()
            if rc != 0 or hit:
                why = f"rc={rc}{describe_signal(rc)}" if rc != 0 else f"error in log, line {hit}"
                rest = [s.tag for s in job.steps[i + 1:]]
                print(f"  !! [{st.tag}] failed ({why})" + (f", skipping: {', '.join(rest)}" if rest else ""))
                job.status = f"FAIL ({st.tag} {why}, log: {log.as_posix()})"
                (job.directory / STATUS_FILE).write_text(f"FAIL {st.tag} {why}\n", encoding="utf-8")
                return False
        job.status = "PASS"
        (job.directory / STATUS_FILE).write_text("PASS\n", encoding="utf-8")
        return True

    # ------------------------------------------------------------------ slurm
    def run_slurm(self, job):
        """Interactive: run with srun and wait. Batch: sbatch and record the job id."""
        cmd = slurm_command(job)
        print(f"  -> {' '.join(shlex.quote(c) for c in cmd)}")
        if job.slurm["mode"] == "interactive":
            rc = subprocess.call(cmd)
            job.status = "PASS" if rc == 0 else f"FAIL (rc={rc})"
            return rc == 0
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            job.status = f"SUBMIT FAILED: {res.stderr.strip()}"
            return False
        # sbatch --parsable prints "<id>" or "<id>;<cluster>"; plain sbatch "Submitted batch job <id>"
        job.slurm_id = res.stdout.strip().split(";")[0].split()[-1] if res.stdout.strip() else "?"
        job.status = f"SUBMITTED (job id {job.slurm_id})"
        return True

    # ------------------------------------------------------------------ all jobs
    def execute(self, jobs):
        """Run or submit every job, print the summary; return the exit code."""
        for job in jobs:
            where = (f"slurm/{job.slurm['mode']}" + ("+gui" if job.slurm["gui"] else "")
                     if job.slurm["enable"] else "local")
            print(f"\n[{job.label}] job={job.name} ({where})\n  dir: {job.directory.as_posix()}")
            if not job.steps:
                job.status = "SKIPPED (no commands)"
                continue
            dep = job.depends_on
            if dep is not None and not dep.status.startswith(("PASS", "SUBMITTED", "DRY")):
                job.status = "SKIPPED (build failed)"
                continue
            if self.args.dry_run:
                for fname in job.files:
                    print(f"  [file] {fname}")
                for st in job.steps:
                    print(f"  [{st.tag}] {st.cmd}")
                if job.slurm["enable"]:
                    print(f"  slurm: {' '.join(shlex.quote(c) for c in slurm_command(job))}")
                job.status = "DRY-RUN"
                continue
            write_script(job)
            ok = self.run_slurm(job) if job.slurm["enable"] else self.run_local(job)
            if not ok and job.kind == "build":
                print(f"  build failed: {job.status}")

        rc = self.summary(jobs)
        if any(j.slurm_id for j in jobs):                  # something was submitted with sbatch
            manifest = self.write_manifest(jobs)
            print(f"slurm jobs recorded in {manifest.as_posix()}")
            if self.args.wait:
                return max(rc, track_jobs(manifest, wait=True, poll=self.args.poll))
            print(f"track them with:  python {Path(sys.argv[0]).name} -c {self.args.config} --track "
                  f"{manifest.as_posix()} [--wait]")
        return rc

    @staticmethod
    def summary(jobs):
        """Print one line per job; return 1 if anything failed."""
        print("\n" + "=" * 78 + "\nSUMMARY")
        width = max(len(j.label) for j in jobs)
        failed = 0
        for j in jobs:
            print(f"  {j.label:<{width}}  {j.status}")
            failed += j.status.startswith(("FAIL", "SUBMIT FAILED", "SKIPPED (build"))
        print("=" * 78)
        return 1 if failed else 0

    # ------------------------------------------------------------------ manifests
    def write_manifest(self, jobs):
        """Record submitted jobs in <out_dir>/jobs/<model>_<time>.yml for --track."""
        submitted = [j for j in jobs if j.slurm_id]
        path = self.flow.jobs_dir() / f"{self.args.model}_{self.flow.time}.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "created": self.flow.now.isoformat(timespec="seconds"),
            "model": self.args.model,
            "config": self.flow.cfg_tpl.path.as_posix(),
            "query": submitted[0].slurm.get("query") or {},
            "poll_interval": submitted[0].slurm.get("poll_interval") or 30,
            "jobs": [{"label": j.label, "name": j.name, "kind": j.kind, "slurm_id": j.slurm_id,
                      "dir": j.directory.as_posix()} for j in submitted],
        }
        path.write_text(yaml.safe_dump(data, sort_keys=False, width=200), encoding="utf-8")
        return path


def latest_manifest(flow):
    """The newest manifest in <out_dir>/jobs (for --track without a file)."""
    found = sorted(flow.jobs_dir().glob("*.yml"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise FlowError(f"no job manifests in {flow.jobs_dir().as_posix()}")
    return found[-1]
