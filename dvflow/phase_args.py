"""Phase arguments: extra or replacement arguments for the steps of a phase.

They come from a testcase's `phase_args:` in the test list and from
-a/--phase-args on the command line. A key has the form

    PHASE[.SUB][.FIELD]      e.g. sim, sim.pre, sim.opts, sim.post.cmd

SUB is pre | main | post (default main). Without FIELD the value is "extra
arguments" added at the end of the step's command; with FIELD (cmd, opts,
args) it edits that part of the step from the config. '=' replaces, '+='
appends (in a test list a trailing '+' on the key means append).
"""

from .constants import SUBPHASE_ALIASES, SUBPHASE_KEYS, SUBPHASES
from .errors import FlowError
from .utils import join_args, to_cmdline


def parse_phase_key(key, known_phases):
    """PHASE[.SUB][.FIELD] -> (phase, sub, field or None).

    'sim'      -> ('sim', 'phase', None)     'sim.pre'      -> ('sim', 'pre_phase', None)
    'sim.opts' -> ('sim', 'phase', 'opts')   'sim.post.cmd' -> ('sim', 'post_phase', 'cmd')
    """
    parts = [p.strip() for p in str(key).strip().split(".")]
    phase, rest = parts[0], parts[1:]
    fld = rest.pop() if rest and rest[-1] in SUBPHASE_KEYS else None
    if len(rest) > 1:
        raise FlowError(f"phase args: bad key '{key}' (use PHASE[.SUB][.FIELD])")
    sub = SUBPHASE_ALIASES.get(rest[0], rest[0]) if rest else "phase"
    if phase not in known_phases:
        raise FlowError(f"phase args: unknown phase '{phase}' (known: {', '.join(known_phases)})")
    if sub not in SUBPHASES:
        raise FlowError(f"phase args: unknown sub-phase '{sub}' in '{key}' (use pre|main|post)")
    return phase, sub, fld


def merge_phase_args(test_phase_args, cli_items, known_phases, selected_phases):
    """Combine test-list phase_args and --phase-args (test list first, then CLI).

    Returns one dict with two kinds of keys:
      "<phase>.<sub>"          -> extra-arguments string
      "<phase>.<sub>.<field>"  -> list of (op, value) edits, op '=' or '+=', applied in order
    Only keys of `selected_phases` are kept.
    """
    out = {}

    def apply(key, op, val):
        phase, sub, fld = parse_phase_key(key, known_phases)
        if fld:
            out.setdefault(f"{phase}.{sub}.{fld}", []).append((op, val))
        else:
            k = f"{phase}.{sub}"
            out[k] = join_args(out.get(k, ""), val) if op == "+=" else val

    # Test list: {"sim": "...", "sim.opts+": "..."}  (trailing '+' = append)
    for key, val in (test_phase_args or {}).items():
        key = str(key).strip()
        apply(key.rstrip("+"), "+=" if key.endswith("+") else "=", to_cmdline(val))
    # Command line: "sim=...", "sim.opts+=..."  (applied last, so it wins)
    for item in cli_items or []:
        if "=" not in item:
            raise FlowError(f"--phase-args expects PHASE[.SUB][.FIELD]=VALUE or ...+=VALUE, got '{item}'")
        key, val = item.split("=", 1)
        apply(key.rstrip("+"), "+=" if key.endswith("+") else "=", val.strip())
    return {k: v for k, v in out.items() if k.split(".")[0] in selected_phases}
