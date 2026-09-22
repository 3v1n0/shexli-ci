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

"""Generate/update a shexli baseline from a JSON report.

Run shexli first, then turn its findings into the baseline consumed by
check_shexli.py / the shexli-ci action:

    shexli --format json extension.zip > report.json
    ./update_baseline.py report.json shexli-baseline.json

The baseline records the findings that are knowingly accepted for a given
input, so it should be regenerated from the same kind of input the check runs
on (e.g. the built ZIP archive, not the source tree).

This script is self-contained and can be copied into a project as-is.
"""

import argparse
import json
import os


def _basename(path):
    # Evidence paths look like "extension.zip:src/foo.js" or "src/foo.js".
    return os.path.basename(path.rsplit(":", 1)[-1])


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


def load_baseline(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        baseline = json.load(f)
    entries = baseline.get("known", baseline if isinstance(baseline, list) else [])
    return {
        (e.get("rule_id"),
         tuple(sorted(e.get("paths", []))),
         tuple(sorted(e.get("snippets", [])))): e
        for e in entries
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="shexli JSON report")
    parser.add_argument("baseline", nargs="?", default="shexli-baseline.json",
                        help="baseline file to write (default: %(default)s)")
    parser.add_argument("--merge", action="store_true",
                        help="keep existing baseline entries that are no longer "
                             "reported (default: drop them)")
    args = parser.parse_args()

    with open(args.report) as f:
        report = json.load(f)

    existing = load_baseline(args.baseline)
    known = {}
    for finding in report.get("findings", []):
        key = _finding_key(finding)
        known[key] = {
            "rule_id": key[0],
            "paths": list(key[1]),
            "snippets": list(key[2]),
        }

    removed = []
    if args.merge:
        for key, entry in existing.items():
            if key not in known:
                removed.append(key)
                known[key] = entry

    baseline = {
        "spec_version": report.get("spec_version"),
        "known": [known[key] for key in sorted(known)],
    }
    with open(args.baseline, "w") as f:
        json.dump(baseline, f, indent=2)
        f.write("\n")

    added = [k for k in known if k not in existing]
    print(f"Wrote {args.baseline}: {len(known)} known finding(s) "
          f"({len(added)} new)")
    for key in removed:
        print(f"  kept (not reported anymore): {key[0]} ({', '.join(key[1])})")


if __name__ == "__main__":
    main()
