#!/usr/bin/env python3

# Copyright (C) 2026 Marco Trevisan (Treviño) <mail@3v1n0.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <https://www.gnu.org/licenses/>.

"""Compare a shexli JSON report against a baseline of known findings.

The baseline lists the findings that are known and accepted, so that CI only
fails when a *new* finding appears (a regression). Findings that were in the
baseline but are no longer reported are just reported as resolved, so the
baseline can be pruned.

A finding is identified by its rule id plus its evidence, using the file base
name (zip/input prefix stripped) and the evidence snippets. This is stable
across line changes, file moves inside the package and different input names.
"""

import argparse
import json
import os
import sys


def _basename(path):
    # Evidence paths look like "extension.zip:src/foo.js" or "src/foo.js".
    path = path.rsplit(":", 1)[-1]
    return os.path.basename(path)


def _finding_key(finding):
    rule_id = finding.get("rule_id")
    paths = tuple(sorted({
        _basename(e.get("path", ""))
        for e in finding.get("evidence", [])
        if e.get("path")
    }))
    snippets = tuple(sorted(
        e.get("snippet", "") for e in finding.get("evidence", [])
    ))
    return rule_id, paths, snippets


def _describe(key):
    rule_id, paths, snippets = key
    detail = ", ".join(paths) or "<no path>"
    if snippets and any(snippets):
        detail += " :: " + ", ".join(s for s in snippets if s)
    return f"{rule_id} ({detail})"


def load_findings(report):
    return {_finding_key(f) for f in report.get("findings", [])}


def load_baseline(baseline):
    entries = baseline.get("known", baseline if isinstance(baseline, list) else [])
    keys = set()
    for entry in entries:
        paths = tuple(sorted(entry.get("paths", [])))
        snippets = tuple(sorted(entry.get("snippets", [])))
        keys.add((entry.get("rule_id"), paths, snippets))
    return keys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="shexli JSON report")
    parser.add_argument("baseline", nargs="?", help="baseline JSON file")
    parser.add_argument("--allow-new", action="store_true",
                        help="do not fail on new findings (report only)")
    args = parser.parse_args()

    with open(args.report) as f:
        report = json.load(f)

    current = load_findings(report)
    summary = report.get("summary", {})

    if not args.baseline or not os.path.exists(args.baseline):
        print("No baseline provided, reporting all findings:")
        for key in sorted(current):
            print(f"  - {_describe(key)}")
        print(f"\n{len(current)} finding(s), "
              f"{summary.get('severity_counts', {})}")
        return 0 if args.allow_new else (1 if current else 0)

    with open(args.baseline) as f:
        baseline = load_baseline(json.load(f))

    new = current - baseline
    resolved = baseline - current

    for key in sorted(resolved):
        print(f"resolved (baseline can be pruned): {_describe(key)}")

    if new:
        print("\nNew findings not present in the baseline:")
        for key in sorted(new):
            print(f"  - {_describe(key)}")

    print(f"\n{len(current)} finding(s): "
          f"{len(new)} new, {len(resolved)} resolved")

    if new and not args.allow_new:
        print("\nERROR: shexli reported new findings.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
