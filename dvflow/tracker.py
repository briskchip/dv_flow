"""Following submitted slurm jobs until they finish (--wait, --track).

Each poll costs one squeue call for all jobs (QUERY_CHUNK ids per call) and
one sacct call for jobs no longer in the queue; then the status files written
by run.csh are read. The result of a job, in order of trust:

  1. dv_flow.status says PASS / FAIL ...           -> that (+ reason from test_status.yml)
  2. squeue lists the job                          -> PENDING / RUNNING ...
  3. sacct has a final state                       -> FAIL (slurm TIMEOUT / CANCELLED / ...)
  4. status file still RUNNING, slurm knows nothing -> FAIL (job died)
"""

import shlex
import subprocess
import time
from pathlib import Path

import yaml

from .constants import QUERY_CHUNK, SLURM_ACTIVE, STATUS_FILE, TEST_STATUS_FILE
from .errors import FlowError


def _query(cmd):
    """stdout of a slurm command; '' if it failed, None if it is not installed."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return res.stdout if res.returncode == 0 else ""


def _chunks(items):
    return (items[i:i + QUERY_CHUNK] for i in range(0, len(items), QUERY_CHUNK))


def slurm_states(query, ids):
    """{job id: slurm state} for the given ids."""
    ids = [i for i in ids if i and i != "?"]
    states = {}
    for chunk in _chunks(ids):                          # queued / running jobs
        out = _query([*shlex.split(query.get("squeue", "squeue")), "-h", "-j", ",".join(chunk), "-o", "%i %T"])
        for line in (out or "").splitlines():
            parts = line.split()
            if len(parts) >= 2:
                states[parts[0]] = parts[1]
    missing = [i for i in ids if i not in states]
    for chunk in _chunks(missing):                      # finished jobs (needs slurm accounting)
        out = _query([*shlex.split(query.get("sacct", "sacct")), "-n", "-P", "-X",
                      "-j", ",".join(chunk), "-o", "JobID,State"])
        for line in (out or "").splitlines():
            parts = line.split("|")
            if len(parts) >= 2 and parts[1].strip():
                states.setdefault(parts[0], parts[1].split()[0])   # "CANCELLED by 123" -> CANCELLED
    return states


def _read(path):
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _first_reason(directory):
    """First reason from check_log.py's test_status.yml, if there is one."""
    try:
        data = yaml.safe_load(_read(directory / TEST_STATUS_FILE) or "") or {}
    except yaml.YAMLError:
        return None
    reasons = data.get("reasons") if isinstance(data, dict) else None
    return reasons[0] if reasons else None


def job_result(job, states):
    """PASS / FAIL ... / PENDING / RUNNING ... for one manifest entry."""
    directory = Path(job["dir"])
    status = _read(directory / STATUS_FILE)
    state = states.get(job["slurm_id"])
    if status and status != "RUNNING":
        result = status                                  # run.csh finished: PASS or FAIL <step>
    elif state in SLURM_ACTIVE:
        return state                                     # still queued or running
    elif state:
        result = f"FAIL (slurm {state})"                 # CANCELLED, TIMEOUT, OUT_OF_MEMORY, NODE_FAIL ...
    elif status == "RUNNING":
        result = "FAIL (job died: not in squeue/sacct)"
    else:
        result = "UNKNOWN (no status file, not in squeue/sacct)"
    if result.startswith("FAIL"):
        reason = _first_reason(directory)
        if reason:
            result += f": {reason}"
    return result


def track_jobs(manifest, wait=False, poll=None):
    """Print the state of the jobs in a manifest; with `wait`, poll until all finish.

    Returns 0 if all passed, 1 if any failed, 3 if some are still running (without wait).
    """
    manifest = Path(manifest)
    try:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise FlowError(f"cannot read job manifest {manifest}: {exc}") from None
    jobs, query = data["jobs"], data.get("query") or {}
    poll = poll or data.get("poll_interval") or 30
    print(f"\ntracking {len(jobs)} slurm job(s) from {manifest.as_posix()}")
    last = None
    while True:
        states = slurm_states(query, [j["slurm_id"] for j in jobs])
        results = [(j, job_result(j, states)) for j in jobs]
        counts = {}
        for _, r in results:
            key = r.split()[0]
            counts[key] = counts.get(key, 0) + 1
        active = any(r in SLURM_ACTIVE for _, r in results)
        progress = "  ".join(f"{k.lower()}={v}" for k, v in sorted(counts.items()))
        if not (wait and active):
            break
        if progress != last:                             # print only when something changed
            print(f"  [{time.strftime('%H:%M:%S')}] {progress}")
            last = progress
        time.sleep(poll)

    print("=" * 78 + "\nSLURM JOBS")
    width = max(len(j["label"]) for j in jobs)
    for j, r in results:
        print(f"  {j['label']:<{width}}  {j['slurm_id']:>10}  {r}")
    print(f"  ({progress})\n" + "=" * 78)
    if any(not (r == "PASS" or r in SLURM_ACTIVE) for _, r in results):
        return 1
    return 3 if active else 0
