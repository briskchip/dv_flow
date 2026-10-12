"""Small helpers with no knowledge of the flow itself."""

import copy
import re
import signal

import yaml

from .errors import FlowError


def split_csv(value):
    """'a, b,,c' -> ['a', 'b', 'c'] (used for --phase and --testcase)."""
    return [v.strip() for v in str(value).split(",") if v.strip()]


def deep_merge(base, over):
    """Return a copy of `base` with `over` merged in.

    Nested dicts are merged key by key; anything else (strings, lists) in
    `over` replaces the value in `base`. Used for `extends:`, for merging the
    master and project files, and for slurm settings.
    """
    out = copy.deepcopy(base)
    for key, val in (over or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def to_cmdline(value):
    """cmd / opts / args may be a string, a list or empty -> one string.

    Empty list items are dropped, so a Jinja expression that renders to ''
    (e.g. "{{ '-gui' if gui else '' }}") leaves no extra space behind.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value if v not in (None, ""))
    return str(value).strip()


def join_args(*parts):
    """Join non-empty command-line pieces with single spaces."""
    return " ".join(p for p in parts if p)


def parse_defines(items):
    """-D KEY=VALUE options -> {KEY: VALUE}.

    Values are read as YAML, so -D count=3 is the number 3, -D cov=true is a
    boolean and -D name=abc is a string.
    """
    defines = {}
    for item in items or []:
        if "=" not in item:
            raise FlowError(f"-D expects KEY=VALUE, got '{item}'")
        key, val = item.split("=", 1)
        defines[key.strip()] = yaml.safe_load(val) if val else ""
    return defines


# ---- slurm time limits ------------------------------------------------------
_DURATION = re.compile(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")      # 1d2h30m10s
_SLURM_TIME = re.compile(r"\d+(?::\d{1,2}){1,2}|\d+-\d{1,2}(?::\d{1,2}){0,2}")  # 01:30:00, 1-12:00


def slurm_time(value, what="timeout"):
    """Convert a user time limit to slurm's --time format.

    Accepts 1h, 90m, 2h30m, 1d12h, 30s, a plain number (minutes, as slurm
    does), slurm's own formats (01:30:00, 1-12:00:00) and 'unlimited'.
    `what` names the setting in error messages.
    """
    text = str(value).strip().lower().replace(" ", "")
    if text in ("unlimited", "infinite"):
        return "UNLIMITED"
    if _SLURM_TIME.fullmatch(text):
        return text                                   # already in slurm format
    if text.isdigit():
        seconds = int(text) * 60                      # plain number = minutes
    else:
        m = _DURATION.fullmatch(text)
        if not text or not m or not any(m.groups()):
            raise FlowError(f"{what}: cannot parse '{value}' (use e.g. 1h, 90m, 2h30m, 45, 01:30:00, 1-00:00:00)")
        d, h, mi, s = (int(g or 0) for g in m.groups())
        seconds = ((d * 24 + h) * 60 + mi) * 60 + s
    if seconds <= 0:
        raise FlowError(f"{what}: must be more than 0, got '{value}'")
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    hms = f"{hours:02}:{minutes:02}:{secs:02}"
    return f"{days}-{hms}" if days else hms


def describe_signal(rc):
    """' (signal TERM)' when an exit code means 'killed by a signal', else ''.

    Python reports a signal as a negative code (-15); a shell reports 128 + n (143).
    """
    num = -rc if rc < 0 else rc - 128 if rc > 128 else 0
    try:
        return f" (signal {signal.Signals(num).name[3:]})" if num else ""
    except ValueError:
        return f" (signal {num})"
