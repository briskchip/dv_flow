#!/usr/bin/env python3
"""
check_log.py - decide whether a simulation passed by scanning its log.

Rules (Python regexes) come from a YAML rules file with named profiles, plus
any extra patterns given on the command line:

  fail     a line matching any of these fails the test
  require  every one of these must match somewhere, or the test fails
           (e.g. the UVM report summary: catches crashes / killed sims)
  ignore   lines matching any of these are skipped (waivers)

Writes <log dir>/test_status.yml and exits 0 (PASS) or 1 (FAIL).
Needs Python 3.8+ and PyYAML (the same as dv_flow.py).

Examples
--------
  check_log.py --log sim.log --rules log_check.yml --profile uvm
  check_log.py --log sim.log --rules log_check.yml --require "TEST PASSED" --ignore "known_issue_123"
  check_log.py --log sim.log --fail "UVM_ERROR" --no-profile      # CLI patterns only
"""

import argparse
import re
import sys
from pathlib import Path

import yaml

MAX_SHOWN = 5  # matching lines reported per pattern


def load_profile(rules_file, profile):
    """Return {fail: [...], require: [...], ignore: [...]} for a profile.

    A profile may `extends:` another profile; its lists are added to the parent's.
    """
    data =yaml.safe_load(Path(rules_file).read_text(encoding="utf-8")) or {}
    profiles = data.get("profiles", data)

    def resolve(name, seen=()):
        if name not in profiles:
            raise SystemExit(f"check_log: profile '{name}' not found in {rules_file} "
                             f"(available: {', '.join(profiles)})")
        if name in seen:
            raise SystemExit(f"check_log: profile extends loop: {' -> '.join(seen + (name,))}")
        spec = profiles[name] or {}
        out = resolve(spec["extends"], seen + (name,)) if spec.get("extends") else {}
        for key in ("fail", "require", "ignore"):
            out[key] = list(out.get(key, [])) + list(spec.get(key) or [])
        return out

    return resolve(profile)


def compile_all(patterns, what):
    out = []
    for pat in patterns:
        try:
            out.append((pat, re.compile(pat)))
        except re.error as exc:
            raise SystemExit(f"check_log: bad {what} regex '{pat}': {exc}")
    return out


def check(log, fail, require, ignore):
    fail_re = compile_all(fail, "fail")
    require_re = compile_all(require, "require")
    ignore_re = compile_all(ignore, "ignore")

    fail_hits = {pat: {"count": 0, "lines": []} for pat, _ in fail_re}
    found = {pat: None for pat, _ in require_re}
    ignored = 0

    with open(log, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, start=1):
            line = line.rstrip("\n")
            if any(rx.search(line) for _, rx in ignore_re):
                ignored += 1
                continue
            for pat, rx in fail_re:
                if rx.search(line):
                    hit = fail_hits[pat]
                    hit["count"] += 1
                    if len(hit["lines"]) < MAX_SHOWN:
                        hit["lines"].append([n, line.strip()])
            for pat, rx in require_re:
                if found[pat] is None and rx.search(line):
                    found[pat] = n

    reasons = []
    for pat, hit in fail_hits.items():
        if hit["count"]:
            reasons.append(f"fail pattern '{pat}' matched {hit['count']}x "
                           f"(first at line {hit['lines'][0][0]}: {hit['lines'][0][1][:120]})")
    for pat, line_no in found.items():
        if line_no is None:
            reasons.append(f"required pattern '{pat}' not found")
    return {
        "status": "FAIL" if reasons else "PASS",
        "reasons": reasons,
        "fail_hits": {p: h for p, h in fail_hits.items() if h["count"]},
        "required": found,
        "ignored_lines": ignored,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="Pass/fail a simulation from its log file.",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("Examples", 1)[1])
    p.add_argument("--log", required=True, help="log file to scan")
    p.add_argument("--rules", help="YAML rules file with profiles")
    p.add_argument("--profile", default="default", help="profile in the rules file (default: default)")
    p.add_argument("--no-profile", action="store_true", help="ignore the rules file, use only CLI patterns")
    p.add_argument("--fail", action="append", default=[], metavar="REGEX", help="extra fail pattern")
    p.add_argument("--require", action="append", default=[], metavar="REGEX", help="extra required pattern")
    p.add_argument("--ignore", action="append", default=[], metavar="REGEX", help="extra ignore (waiver) pattern")
    p.add_argument("--status-file", help="where to write the result (default: <log dir>/test_status.yml)")
    p.add_argument("--test", default="", help="test name, recorded in the status file")
    args = p.parse_args(argv)

    rules = {"fail": [], "require": [], "ignore": []}
    if args.rules and not args.no_profile:
        rules = load_profile(args.rules, args.profile)
    fail = rules["fail"] + args.fail
    require = rules["require"] + args.require
    ignore = rules["ignore"] + args.ignore

    log = Path(args.log)
    if log.is_file():
        result = check(log, fail, require, ignore)
    else:
        result = {"status": "FAIL", "reasons": [f"log file not found: {log}"],
                  "fail_hits": {}, "required": {}, "ignored_lines": 0}
    result.update({"test": args.test, "log": str(log.resolve()),
                   "profile": None if args.no_profile or not args.rules else args.profile})

    # Result for people and for dv_flow's tracker (it shows the first reason).
    status_file = Path(args.status_file) if args.status_file else log.resolve().parent / "test_status.yml"
    status_file.write_text(yaml.safe_dump(result, sort_keys=False, width=200), encoding="utf-8")

    name = f" {args.test}" if args.test else ""
    print(f"check_log:{name} {result['status']}  ({log})")
    for reason in result["reasons"]:
        print(f"  - {reason}")
    for pat, hit in result["fail_hits"].items():
        for n, text in hit["lines"][1:]:
            print(f"    line {n}: {text[:160]}")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
