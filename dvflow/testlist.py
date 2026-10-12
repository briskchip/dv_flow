"""Test lists: loading testcases, selecting them, and expanding seeds."""

import fnmatch
import random

from .errors import FlowError
from .templates import YamlTemplate
from .utils import split_csv


def load_testlist(path, ctx, defines, include_dirs=()):
    """Read a test list file. Returns (tests, sections).

    Each test is a dict with name, testname, description, section, phase_args,
    seed, count, coverage, plus every extra field written on the entry
    (verbosity, slurm_timeout, log_profile, ...), which the config can use as
    {{ test.<field> }}.

    A section is either a list of tests, or a mapping with defaults for its
    tests plus `tests:`; a test's own fields win over the section's.
    """
    tpl = YamlTemplate(path, include_dirs)
    data, _ = tpl.render(ctx, defines)
    sections = data.get("sections")
    if sections is None:  # allow sections at the top level too
        sections = {k: v for k, v in data.items() if k not in ("variables", "anchors")}

    tests, seen = [], {}
    for section, entries in (sections or {}).items():
        defaults = {}
        if isinstance(entries, dict):
            defaults = {k.replace("-", "_"): v for k, v in entries.items() if k != "tests"}
            entries = entries.get("tests")
        for raw in entries or []:
            if isinstance(raw, dict):
                # field names may use '-' or '_' (slurm-timeout == slurm_timeout)
                raw = {str(k).replace("-", "_"): v for k, v in raw.items()}
                # section defaults first, then the test; phase_args are merged key by key
                pa = {**(defaults.get("phase_args") or {}), **(raw.get("phase_args") or {})}
                raw = {**defaults, **raw, "phase_args": pa}
            if not isinstance(raw, dict) or not raw.get("testcase"):
                raise FlowError(f"{tpl.path}: section '{section}': every entry needs a 'testcase' name")
            name = str(raw["testcase"])
            if name in seen:
                raise FlowError(f"{tpl.path}: duplicate testcase '{name}' (sections '{seen[name]}' and '{section}')")
            seen[name] = section
            phase_args = raw.get("phase_args") or {}
            if not isinstance(phase_args, dict):
                raise FlowError(f"{tpl.path}: testcase '{name}': phase_args must be a mapping phase -> args")
            extra = {k: v for k, v in raw.items() if k not in ("testcase", "name", "section")}
            tests.append({
                **extra,
                "name": name,
                "testname": str(raw.get("testname") or name),
                "description": raw.get("description", ""),
                "section": section,
                "phase_args": phase_args,
                "seed": raw.get("seed", "random"),
                "count": int(raw.get("count", 1)),
                "coverage": bool(raw.get("coverage", False)),
            })
    return tests, sections


def select_tests(tests, sections, selection, model):
    """Pick tests for --testcase: names, section names, globs (rand_*) or 'all'.

    Keeps the order of the selection and never returns a test twice.
    """
    picked, names = [], set()
    for token in split_csv(selection):
        if token == "all":
            matches = tests
        elif token in sections:
            matches = [t for t in tests if t["section"] == token]
        else:
            matches = [t for t in tests if fnmatch.fnmatchcase(t["name"], token)]
        if not matches:
            raise FlowError(f"test '{token}' matches no testcase or section in model '{model}' "
                            f"(sections: {', '.join(sections)})")
        for t in matches:
            if t["name"] not in names:
                names.add(t["name"])
                picked.append(t)
    return picked


def seeds_for(test, seed_override=None, count_override=None):
    """The seeds to run for a test.

    seed: 5, count: 3 -> [5, 6, 7];  seed: random -> a new random seed per run.
    --seed / --count override the test list.
    """
    count = count_override if count_override is not None else test["count"]
    seed = seed_override if seed_override is not None else test["seed"]
    if str(seed).lower() == "random":
        return [random.randint(1, 2**31 - 1) for _ in range(count)]
    try:
        seed = int(seed)
    except ValueError:
        raise FlowError(f"testcase '{test['name']}': seed must be an integer or 'random', got '{seed}'") from None
    return [seed + i for i in range(count)]
