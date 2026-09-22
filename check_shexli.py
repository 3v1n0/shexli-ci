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
baseline but are no longer reported are reported as resolved, so the baseline
can be pruned; depending on ``--on-resolved`` they are warned about (the
default) or make the check fail.

A finding is identified by its rule id plus its evidence, using the file base
name (zip/input prefix stripped) and the evidence snippets. This is stable
across line changes, file moves inside the package and different input names.

Individual occurrences can also be acknowledged in the source itself::

    // shexli-ci: EGO-I-004 - the symbol is needed to reimplement the vfunc
    prototype = prototype[Gi.hook_up_vfunc_symbol];

or as a trailing comment on the offending line::

    prototype = prototype[Gi.hook_up_vfunc_symbol]; // shexli-ci: EGO-I-004 - needed
    this._staticBox = new Clutter.ActorBox(); /* shexli-ci: EGO-L-002 - sized later */

A directive that shares its line with code (a trailing ``//`` comment, or a
``/* */`` comment surrounded by code) acknowledges that line. A directive alone
on its line acknowledges the next code line, skipping blank and comment-only
lines. The directive must name one or more exact rule ids (comma or space
separated) and must carry a non-empty rationale after `` - ``. Acknowledged
occurrences are still reported and annotated, but as warnings, and never block
the check. Only the acknowledged occurrence is ignored: the other occurrences
of the same finding keep blocking. A directive without a rationale is invalid,
acknowledges nothing and fails the check.

When running inside GitHub Actions (GITHUB_ACTIONS is set), new findings are
also emitted as workflow annotations (errors on their source line, warnings
for the acknowledged occurrences) and a job summary is written to
GITHUB_STEP_SUMMARY.

When running inside GitLab CI (GITLAB_CI is set), the log uses collapsible
sections and, with ``--codequality``, a Code Quality report can be written for
the merge request. ``--summary`` writes the markdown summary to an arbitrary
file, which is also how the GitLab job exposes it as an artifact.
"""

import argparse
import hashlib
import itertools
import json
import os
import re
import sys
import time
import zipfile


_IGNORE_TOKEN = "shexli-ci:"
_RULE_SEPARATOR = re.compile(r"[,\s]+")
_DIRECTIVE = re.compile(r"^(?P<rules>.+?)\s+-\s+(?P<rationale>\S.*)$", re.DOTALL)
# Directives use JS-style comments, so only source-like files are scanned.
_DIRECTIVE_EXTENSIONS = frozenset({
    ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".css", ".scss", ".sass",
})
_SECTION_IDS = itertools.count()
# Code Quality severities, from most to least severe.
_CODEQUALITY_SEVERITY = {"error": "major", "warning": "minor"}


def _ci_provider():
    """Return 'github', 'gitlab' or None depending on the CI environment."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return "github"
    if os.environ.get("GITLAB_CI") == "true":
        return "gitlab"
    return None


def _gitlab_section_start(name, title):
    print(f"\x1b[0Ksection_start:{int(time.time())}:{name}\r\x1b[0K{title}")


def _gitlab_section_end(name):
    print(f"\x1b[0Ksection_end:{int(time.time())}:{name}\r\x1b[0K")


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


def _iter_comments(text):
    """Yield (start, end, kind) for every JS comment in ``text``.

    ``kind`` is ``"line"`` for ``//`` comments and ``"block"`` for ``/* */``
    ones. Strings and template literals are skipped so that comment markers
    inside them are not mistaken for comments.
    """
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "/" and index + 1 < length:
            following = text[index + 1]
            if following == "/":
                end = text.find("\n", index)
                end = length if end == -1 else end
                yield index, end, "line"
                index = end + 1
                continue
            if following == "*":
                end = text.find("*/", index + 2)
                end = length if end == -1 else end + 2
                yield index, end, "block"
                index = end
                continue
        if char in "\"'`":
            quote = char
            index += 1
            while index < length:
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        index += 1


def _code_lines(text, comments):
    """Return the 1-based line numbers that contain code outside comments."""
    parts = []
    last = 0
    for start, end, _kind in sorted(comments):
        parts.append(text[last:start])
        parts.append("".join(
            "\n" if char == "\n" else " " for char in text[start:end]))
        last = end
    parts.append(text[last:])
    masked = "".join(parts)
    return {
        number for number, line in enumerate(masked.split("\n"), 1)
        if line.strip()
    }


def _next_code_line(code_lines, after, last_line):
    """Return the first code line at or after ``after``, if any."""
    for line in range(after, last_line + 1):
        if line in code_lines:
            return line
    return None


def _parse_directives(text):
    """Extract the shexli-ci directives of one source file.

    Return ``(covered, malformed)`` where ``covered`` maps a 1-based line
    number to the list of ``(rule_ids, rationale)`` directives acknowledging
    findings on that line, and ``malformed`` is a list of ``(line, body)`` for
    directives that could not be parsed (e.g. missing rationale).

    A directive sharing its line with code (a trailing ``//`` comment, or a
    ``/* */`` comment surrounded by code) acknowledges that line. A directive
    alone on its line acknowledges the next code line, skipping blank and
    comment-only lines.
    """
    covered = {}
    malformed = []
    comments = list(_iter_comments(text))
    code_lines = _code_lines(text, comments)
    last_line = text.count("\n") + 1

    for start, end, kind in comments:
        comment = text[start:end]
        index = comment.find(_IGNORE_TOKEN)
        if index == -1:
            continue

        body = comment[index + len(_IGNORE_TOKEN):]
        if kind == "block" and "*/" in body:
            body = body[:body.rfind("*/")]
        body = body.strip()

        match = _DIRECTIVE.match(body)
        rules = _RULE_SEPARATOR.split(match.group("rules").strip()) if match else []
        rationale = match.group("rationale").strip() if match else ""
        if not match or not rules or not rationale:
            malformed.append((text.count("\n", 0, start) + 1, body))
            continue

        start_line = text.count("\n", 0, start) + 1
        end_line = text.count("\n", 0, end) + 1
        if start_line in code_lines:
            # Code on the same line as the directive: acknowledge that line.
            covered_line = start_line
        elif end_line in code_lines:
            covered_line = end_line
        else:
            # Directive alone on its line(s): acknowledge the next code line.
            covered_line = _next_code_line(code_lines, end_line + 1, last_line)
        if covered_line is None:
            continue
        covered.setdefault(covered_line, []).append((rules, rationale))
    return covered, malformed


class _Source:
    """Read package-relative sources from a directory or ZIP archive."""

    def __init__(self, source):
        self.source = source
        self._zip = None
        self._cache = {}
        if source and os.path.isfile(source) and zipfile.is_zipfile(source):
            self._zip = zipfile.ZipFile(source)

    def _candidates(self, display_path):
        if self._zip is not None:
            member = display_path.split(":", 1)[1] if ":" in display_path else display_path
            return [member]
        candidates = [display_path]
        if self.source:
            # The evidence path may already be relative to the current
            # directory (the usual case), or include the analyzed path prefix,
            # or be package-relative while the analysis ran elsewhere.
            candidates.append(os.path.join(self.source, display_path))
            try:
                relative = os.path.relpath(display_path, self.source)
            except ValueError:
                pass
            else:
                candidates.append(os.path.join(self.source, relative))
                candidates.append(relative)
        return candidates

    def read(self, display_path):
        if display_path in self._cache:
            return self._cache[display_path]
        text = None
        if self._zip is not None:
            try:
                with self._zip.open(self._candidates(display_path)[0]) as handle:
                    text = handle.read().decode("utf-8", "replace")
            except (KeyError, OSError, zipfile.BadZipFile):
                text = None
        else:
            for candidate in self._candidates(display_path):
                try:
                    with open(candidate, encoding="utf-8", errors="replace") as handle:
                        text = handle.read()
                    break
                except OSError:
                    continue
        self._cache[display_path] = text
        return text


def _acknowledged(directives_for_line, rule_id):
    """Return the rationale acknowledging ``rule_id`` on a line, if any."""
    if not directives_for_line or not rule_id:
        return None
    for rules, rationale in directives_for_line:
        if any(rule.lower() == rule_id.lower() for rule in rules):
            return rationale
    return None


def _acknowledged_occurrences(finding, directives):
    """Map evidence indexes of ``finding`` to their acknowledging rationale."""
    acknowledged = {}
    for index, ev in enumerate(finding.get("evidence", [])):
        path = ev.get("path")
        line = ev.get("line")
        if not path or not line:
            continue
        rationale = _acknowledged(
            directives.get(path, {}).get(line), finding.get("rule_id"))
        if rationale:
            acknowledged[index] = rationale
    return acknowledged


def _readable_source(path):
    """Whether ``path`` uses a JS-style comment syntax worth scanning."""
    extension = os.path.splitext(_display_path(path))[1].lower()
    return extension in _DIRECTIVE_EXTENSIONS


def _collect_directives(source, findings):
    """Scan the evidence files of ``findings`` for shexli-ci directives."""
    if not source or not os.path.exists(source):
        return {}, []

    reader = _Source(source)
    directives = {}
    malformed = []
    seen = set()
    for finding in findings:
        for ev in finding.get("evidence", []):
            path = ev.get("path")
            if not path or path in seen or not _readable_source(path):
                continue
            seen.add(path)
            text = reader.read(path)
            if text is None:
                continue
            covered, bad = _parse_directives(text)
            if covered:
                directives[path] = covered
            malformed.extend((path, line, body) for line, body in bad)
    return directives, malformed


def _unique_occurrences(finding, acknowledged=None):
    """Collapse evidence entries that share a location.

    shexli can report several evidence entries for the same file and line (for
    example a whole statement and a nested call), which would otherwise become
    identical warnings or annotations. Keep one occurrence per location,
    preferring the entry with the longest snippet. Entries without a path are
    kept as they are.

    Yield ``(evidence, rationale)`` pairs.
    """
    acknowledged = acknowledged or {}
    unique = {}
    for index, ev in enumerate(finding.get("evidence", [])):
        path = ev.get("path")
        key = (path, ev.get("line")) if path else ("", index)
        rationale = acknowledged.get(index)
        if key not in unique:
            unique[key] = [ev, rationale]
            continue
        entry = unique[key]
        if len(ev.get("snippet") or "") > len(entry[0].get("snippet") or ""):
            entry[0] = ev
        if rationale and not entry[1]:
            entry[1] = rationale
    for ev, rationale in unique.values():
        yield ev, rationale


def _print_finding(finding, key, indent="  - ", acknowledged=None):
    """Print a finding, one collapsible group per occurrence in CI."""
    acknowledged = acknowledged or {}
    rule_id = finding.get("rule_id", key[0])
    severity = finding.get("severity", "warning")
    message = _oneline(finding.get("message", ""))
    provider = _ci_provider()
    occurrences = list(_unique_occurrences(finding, acknowledged)) or [({}, None)]

    if provider is None:
        print(f"{indent}{rule_id} [{severity}]: {message}")
        for ev, rationale in occurrences:
            note = f"  [acknowledged: {rationale}]" if rationale else ""
            print(f"      {_evidence_line(ev)}{note}")
        return

    # Each occurrence gets its own collapsible group with its details. The
    # group already keeps the log tidy, so the snippet is shown as it is.
    for index, (ev, rationale) in enumerate(occurrences):
        label = f"{rule_id} [{severity}]"
        if rationale:
            label += " (acknowledged inline)"
        note = f" — rationale: {rationale}" if rationale else ""
        section = f"shexli-{next(_SECTION_IDS)}"
        if provider == "github":
            print(f"::group::{label}: {message}{note}")
        else:
            _gitlab_section_start(section, f"{label}: {message}{note}")
        print(f"    {_evidence_location(ev)}")
        snippet = ev.get("snippet", "").rstrip()
        if snippet:
            print("\n".join(f"    {line}" for line in snippet.splitlines()))
        if provider == "github":
            print("::endgroup::")
        else:
            _gitlab_section_end(section)


def _annotate(finding, acknowledged=None):
    """Emit a GitHub workflow annotation for each occurrence of a finding."""
    acknowledged = acknowledged or {}
    rule_id = finding.get("rule_id", "shexli")
    severity = finding.get("severity", "warning")
    message = finding.get("message", "").replace("\n", " ")

    emitted = False
    for ev, rationale in _unique_occurrences(finding, acknowledged):
        path = ev.get("path")
        if not path:
            continue
        if rationale:
            # Acknowledged occurrences are still shown, but only as warnings
            # and never counted as a failure.
            level = "warning"
            title = f"{rule_id} (acknowledged)"
            text = f"{message} (acknowledged: {rationale})"
        else:
            level = "error" if severity == "error" else "warning"
            title = rule_id
            text = message
        props = [f"title={title}", f"file={_display_path(path)}"]
        line = ev.get("line")
        if line:
            props.append(f"line={line}")
        print(f"::{level} {','.join(props)}::{text}")
        emitted = True

    if not emitted:
        level = "error" if severity == "error" else "warning"
        print(f"::{level} title={rule_id}::{message}")


def _annotate_malformed(path, line, body):
    """Emit a warning annotation for an unusable shexli-ci directive."""
    props = ["title=shexli-ci"]
    if path:
        props.append(f"file={_display_path(path)}")
    if line:
        props.append(f"line={line}")
    message = (f"malformed shexli-ci directive: '{_IGNORE_TOKEN} {body}' "
               f"(a rationale after ' - ' is required)")
    print(f"::error {','.join(props)}::{message}")


def _codequality_path(path):
    """Return a repository-relative path for a Code Quality entry, if any."""
    if os.path.isabs(path):
        try:
            path = os.path.relpath(path, os.getcwd())
        except ValueError:
            return None
    while path.startswith("./"):
        path = path[2:]
    if not path or path == "." or path.startswith("../"):
        return None
    return path


def _codequality_fingerprint(rule_id, path, snippet, line):
    basis = f"{rule_id}\0{path}\0{line}\0{snippet}"
    return hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()


def _write_codequality(report_path, new_keys, indexed, acknowledged_by_key=None):
    """Write a GitLab Code Quality report for the new findings.

    Only findings that are not in the baseline are reported, matching the
    annotations; acknowledged occurrences are kept, with an ``info`` severity
    and their rationale, so they stay visible in the merge request without
    failing it.
    """
    acknowledged_by_key = acknowledged_by_key or {}
    entries = []
    seen = set()
    for key in sorted(new_keys):
        finding = indexed[key]
        acknowledged = acknowledged_by_key.get(key) or {}
        rule_id = finding.get("rule_id", key[0])
        severity = finding.get("severity", "warning")
        message = _oneline(finding.get("message", ""))
        for ev, rationale in _unique_occurrences(finding, acknowledged):
            raw_path = ev.get("path")
            if not raw_path:
                continue
            path = _codequality_path(_display_path(raw_path))
            if not path:
                continue
            line = ev.get("line") or 1
            fingerprint = _codequality_fingerprint(
                rule_id, path, ev.get("snippet") or "", line)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            if rationale:
                description = f"{message} (acknowledged: {rationale})"
                level = "info"
            else:
                description = message
                level = _CODEQUALITY_SEVERITY.get(severity, "minor")
            entries.append({
                "description": description,
                "check_name": rule_id,
                "fingerprint": fingerprint,
                "severity": level,
                "location": {"path": path, "lines": {"begin": line}},
            })

    with open(report_path, "w") as f:
        json.dump(entries, f, indent=2)
        f.write("\n")
    return entries


def _write_summary(report, new_keys, resolved_keys, accepted_keys, indexed,
                   has_baseline=True, blocking=None, ignored=None,
                   acknowledged=None, malformed=(), on_resolved="warn",
                   path=None):
    summary_path = path or os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    blocking = blocking if blocking is not None else new_keys
    ignored = ignored or set()
    acknowledged = acknowledged or {}

    counts = report.get("summary", {}).get("severity_counts", {})
    lines = ["## shexli"]
    lines.append("")
    if not has_baseline:
        if blocking:
            lines.append(f"❌ **{len(blocking)} finding(s)** reported")
        elif ignored:
            lines.append(f"⚠️ {len(ignored)} finding(s) acknowledged inline")
        else:
            lines.append("✅ No findings")
    elif blocking:
        lines.append(f"❌ **{len(blocking)} new finding(s)** not in the baseline")
    elif ignored:
        lines.append(f"⚠️ No blocking findings; "
                     f"{len(ignored)} new finding(s) acknowledged inline")
    else:
        lines.append("✅ No new findings")
    if resolved_keys:
        if on_resolved == "fail":
            lines.append(f"❌ **{len(resolved_keys)} baseline entry(ies) "
                         f"no longer apply**")
        else:
            lines.append(f"⚠️ {len(resolved_keys)} baseline entry(ies) "
                         f"can be pruned")
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

    def acknowledged_rows(keys):
        rows = ["| Rule | Severity | Message | Files | Rationale |",
                "| --- | --- | --- | --- | --- |"]
        for key in sorted(keys):
            finding = indexed.get(key, {})
            rule_id = finding.get("rule_id", key[0])
            severity = finding.get("severity", "warning")
            message = finding.get("message", "").replace("\n", " ")
            paths = ", ".join(key[1]) or "-"
            rationale = "; ".join(sorted({
                r for r in acknowledged.get(key, {}).values() if r}))
            rows.append(f"| `{rule_id}` | {severity} | {message} | {paths} | "
                        f"{rationale} |")
        return rows

    if blocking:
        lines.append("### New findings" if has_baseline else "### Findings")
        lines.extend(finding_rows(blocking))
        lines.append("")
    if ignored:
        lines.append(f"### Acknowledged inline ({len(ignored)})")
        lines.append("")
        lines.extend(acknowledged_rows(ignored))
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
        if on_resolved == "fail":
            lines.append("### Resolved (baseline entries no longer apply)")
        else:
            lines.append("### Resolved (baseline can be pruned)")
        lines.append("")
        lines.append(", ".join(f"`{k[0]}` ({', '.join(k[1])})"
                              for k in sorted(resolved_keys)))
        lines.append("")
    if malformed:
        lines.append(f"### Malformed shexli-ci directives ({len(malformed)})")
        lines.append("")
        for path, line, body in sorted(malformed):
            lines.append(f"- `{_display_path(path)}:{line}`: "
                         f"`{_IGNORE_TOKEN} {body}` "
                         f"(a rationale after ' - ' is required)")
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
    parser.add_argument("--source",
                        help="extension directory or ZIP archive that was "
                             "analyzed, used to read the inline shexli-ci "
                             "directives (defaults to the report input_path)")
    parser.add_argument("--on-resolved", choices=("warn", "fail"),
                        default="warn",
                        help="what to do when a baseline entry no longer "
                             "applies: 'warn' reports it without failing, "
                             "'fail' makes the check fail (default: %(default)s)")
    parser.add_argument("--summary",
                        help="write the markdown job summary to this file "
                             "(defaults to GITHUB_STEP_SUMMARY when set)")
    parser.add_argument("--codequality",
                        help="write a GitLab Code Quality report for the new "
                             "findings to this file")
    args = parser.parse_args()

    with open(args.report) as f:
        report = json.load(f)

    indexed = _index_findings(report)
    current = set(indexed)
    summary = report.get("summary", {})
    source = args.source or summary.get("input_path")

    provider = _ci_provider()
    annotate = provider == "github" and not args.no_annotate

    directives, malformed = _collect_directives(
        source, [indexed[key] for key in sorted(current)])

    has_baseline = bool(args.baseline) and os.path.exists(args.baseline)
    if has_baseline:
        with open(args.baseline) as f:
            baseline = load_baseline(json.load(f))
    else:
        baseline = set()
        print("No baseline provided, reporting all findings:")

    new = current - baseline
    resolved = baseline - current
    accepted = current & baseline

    acknowledged = {}
    blocking = set()
    ignored = set()
    for key in new:
        matches = _acknowledged_occurrences(indexed[key], directives)
        if matches:
            acknowledged[key] = matches
        evidence = indexed[key].get("evidence", [])
        if matches and len(matches) == len(evidence):
            ignored.add(key)
        else:
            blocking.add(key)

    resolved_level = "warning" if args.on_resolved == "warn" else "error"
    for key in sorted(resolved):
        print(f"{resolved_level}: baseline entry no longer applies "
              f"(can be pruned): {_describe(key)}")

    if blocking:
        label = ("New findings not present in the baseline"
                 if has_baseline else "Findings")
        print(f"\n{label} ({len(blocking)}):")
        for key in sorted(blocking):
            _print_finding(indexed[key], key, acknowledged=acknowledged.get(key))
            if annotate:
                _annotate(indexed[key], acknowledged=acknowledged.get(key))

    if ignored:
        print(f"\nAcknowledged inline, non-blocking ({len(ignored)}):")
        for key in sorted(ignored):
            _print_finding(indexed[key], key, acknowledged=acknowledged.get(key))
            if annotate:
                _annotate(indexed[key], acknowledged=acknowledged.get(key))

    if accepted:
        print(f"\nAccepted (known) findings ({len(accepted)}):")
        for key in sorted(accepted):
            _print_finding(indexed[key], key)

    if malformed:
        print(f"\nMalformed shexli-ci directives ({len(malformed)}):")
        for path, line, body in sorted(malformed):
            print(f"  {_display_path(path)}:{line}: "
                  f"{_IGNORE_TOKEN} {body!r} "
                  f"(a rationale after ' - ' is required)")
            if annotate:
                _annotate_malformed(path, line, body)

    print(f"\n{len(current)} finding(s): "
          f"{len(new)} new, {len(resolved)} resolved, {len(accepted)} accepted"
          + (f", {len(ignored)} acknowledged" if ignored else "")
          + (f", {len(malformed)} malformed directive(s)" if malformed else ""))

    _write_summary(report, new, resolved, accepted, indexed,
                   has_baseline=has_baseline, blocking=blocking, ignored=ignored,
                   acknowledged=acknowledged, malformed=malformed,
                   on_resolved=args.on_resolved, path=args.summary)

    if args.codequality:
        entries = _write_codequality(args.codequality, new, indexed, acknowledged)
        print(f"\nWrote Code Quality report: {args.codequality} "
              f"({len(entries)} issue(s))")

    if malformed:
        print("\nERROR: malformed shexli-ci directive(s).")
        return 1
    if blocking and not args.allow_new:
        print("\nERROR: shexli reported new findings.")
        return 1
    if resolved and args.on_resolved == "fail":
        print("\nERROR: baseline entries no longer apply.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
