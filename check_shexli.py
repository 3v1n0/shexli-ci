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

When running inside GitHub Actions (GITHUB_ACTIONS is set), new findings are
also emitted as workflow annotations (errors on their source line) and a job
summary is written to GITHUB_STEP_SUMMARY.
"""

import argparse
import json
import os
import sys


def _basename(path):
    # Evidence paths look like "extension.zip:src/foo.js" or "src/foo.js".
    path = path.rsplit(":", 1)[-1]
    return os.path.basename(path)


def _display_path(path):
    # Same as _basename() but keeping the package-relative directories, so the
    # annotation can point at the file in the repository.
    return path.rsplit(":", 1)[-1]


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


def _oneline(text):
    return " ".join(text.split())


def _describe(key):
    rule_id, paths, snippets = key
    detail = ", ".join(paths) or "<no path>"
    useful = [s for s in snippets if s]
    if useful:
        detail += " :: " + ", ".join(_oneline(s) for s in useful)
    return f"{rule_id} ({detail})"


def _print_finding(finding, key, indent="  - "):
    """Print a finding, one group per occurrence when on GitHub Actions."""
    rule_id = finding.get("rule_id", key[0])
    severity = finding.get("severity", "warning")
    message = _oneline(finding.get("message", ""))
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    evidence = finding.get("evidence", []) or [{}]

    if not in_actions:
        print(f"{indent}{rule_id} [{severity}]: {message}")
        for ev in evidence:
            print(f"      {_evidence_line(ev)}")
        return

    # Each occurrence gets its own collapsible group with its details. The
    # group already keeps the log tidy, so the snippet is shown as it is.
    for ev in evidence:
        print(f"::group::{rule_id} [{severity}]: {message}")
        print(f"    {_evidence_location(ev)}")
        snippet = ev.get("snippet", "").rstrip()
        if snippet:
            print("\n".join(f"    {line}" for line in snippet.splitlines()))
        print("::endgroup::")


def _evidence_location(ev):
    path = ev.get("path")
    line = ev.get("line")
    location = path.rsplit(":", 1)[-1] if path else "-"
    if line:
        location += f":{line}"
    return location


def _evidence_line(ev):
    snippet = _oneline(ev.get("snippet", ""))
    location = _evidence_location(ev)
    return location + (f"  {snippet}" if snippet else "")


def _index_findings(report):
    """Map each finding key to its finding object(s)."""
    indexed = {}
    for finding in report.get("findings", []):
        indexed.setdefault(_finding_key(finding), finding)
    return indexed


def load_baseline(baseline):
    entries = baseline.get("known", baseline if isinstance(baseline, list) else [])
    keys = set()
    for entry in entries:
        paths = tuple(sorted(entry.get("paths", [])))
        snippets = tuple(sorted(entry.get("snippets", [])))
        keys.add((entry.get("rule_id"), paths, snippets))
    return keys


def _annotate(finding):
    """Emit a GitHub workflow annotation for each occurrence of a finding."""
    rule_id = finding.get("rule_id", "shexli")
    severity = finding.get("severity", "warning")
    level = "error" if severity == "error" else "warning"
    message = finding.get("message", "").replace("\n", " ")

    evidence = [ev for ev in finding.get("evidence", []) if ev.get("path")]
    if not evidence:
        evidence = [{}]
    for ev in evidence:
        path = ev.get("path")
        line = ev.get("line")
        props = [f"title={rule_id}"]
        if path:
            props.append(f"file={_display_path(path)}")
        if line:
            props.append(f"line={line}")
        print(f"::{level} {','.join(props)}::{message}")


def _write_summary(report, new_keys, resolved_keys, accepted_keys, indexed,
                   has_baseline=True):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    counts = report.get("summary", {}).get("severity_counts", {})
    lines = ["## shexli"]
    lines.append("")
    if not has_baseline:
        lines.append(f"⚠️ No baseline: {len(new_keys)} finding(s) reported")
    elif new_keys:
        lines.append(f"❌ **{len(new_keys)} new finding(s)** not in the baseline")
    else:
        lines.append("✅ No new findings")
    lines.append("")

    def finding_rows(keys):
        rows = ["| Rule | Severity | Message | Files |",
                "| --- | --- | --- | --- |"]
        for key in sorted(keys):
            finding = indexed.get(key, {})
            rule_id = finding.get("rule_id", key[0])
            severity = finding.get("severity", "warning")
            message = finding.get("message", "").replace("\n", " ")
            paths = ", ".join(key[1]) or "-"
            rows.append(f"| `{rule_id}` | {severity} | {message} | {paths} |")
        return rows

    if new_keys:
        lines.append("### New findings" if has_baseline else "### Findings")
        lines.extend(finding_rows(new_keys))
        lines.append("")
    if accepted_keys:
        lines.append(f"<details><summary>Accepted findings "
                     f"({len(accepted_keys)})</summary>")
        lines.append("")
        lines.extend(finding_rows(accepted_keys))
        lines.append("")
        lines.append("</details>")
        lines.append("")
    if resolved_keys:
        lines.append("### Resolved (baseline can be pruned)")
        lines.append("")
        lines.append(", ".join(f"`{k[0]}` ({', '.join(k[1])})"
                              for k in sorted(resolved_keys)))
        lines.append("")

    lines.append(f"Total: {counts.get('error', 0)} error(s), "
                 f"{counts.get('warning', 0)} warning(s)")
    lines.append("")

    with open(summary_path, "a") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="shexli JSON report")
    parser.add_argument("baseline", nargs="?", help="baseline JSON file")
    parser.add_argument("--allow-new", action="store_true",
                        help="do not fail on new findings (report only)")
    parser.add_argument("--no-annotate", action="store_true",
                        help="do not emit GitHub workflow annotations")
    args = parser.parse_args()

    with open(args.report) as f:
        report = json.load(f)

    indexed = _index_findings(report)
    current = set(indexed)
    summary = report.get("summary", {})

    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    annotate = in_actions and not args.no_annotate

    if not args.baseline or not os.path.exists(args.baseline):
        print("No baseline provided, reporting all findings:")
        for key in sorted(current):
            _print_finding(indexed[key], key)
            if annotate:
                _annotate(indexed[key])
        print(f"\n{len(current)} finding(s), "
              f"{summary.get('severity_counts', {})}")
        _write_summary(report, current, set(), set(), indexed,
                       has_baseline=False)
        return 0 if args.allow_new else (1 if current else 0)

    with open(args.baseline) as f:
        baseline = load_baseline(json.load(f))

    new = current - baseline
    resolved = baseline - current
    accepted = current & baseline

    for key in sorted(resolved):
        print(f"resolved (baseline can be pruned): {_describe(key)}")

    if new:
        print(f"\nNew findings not present in the baseline ({len(new)}):")
        for key in sorted(new):
            _print_finding(indexed[key], key)
            if annotate:
                _annotate(indexed[key])

    if accepted:
        print(f"\nAccepted (known) findings ({len(accepted)}):")
        for key in sorted(accepted):
            _print_finding(indexed[key], key)

    print(f"\n{len(current)} finding(s): "
          f"{len(new)} new, {len(resolved)} resolved, {len(accepted)} accepted")

    _write_summary(report, new, resolved, accepted, indexed)

    if new and not args.allow_new:
        print("\nERROR: shexli reported new findings.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
