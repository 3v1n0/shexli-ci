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

"""Tests for check_shexli.py, focused on the inline shexli-ci directives.

Run with ``python3 -m unittest test_check_shexli`` (or ``python3
test_check_shexli.py``). They do not need shexli: the reports are hand-written.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_shexli  # noqa: E402


CHECK = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "check_shexli.py")


def finding(rule_id, path, lines, snippet="code"):
    return {
        "rule_id": rule_id,
        "title": rule_id,
        "severity": "warning",
        "message": f"{rule_id} message",
        "evidence": [
            {"path": path, "line": line, "snippet": snippet} for line in lines
        ],
    }


def report(findings, input_path):
    return {
        "spec_version": "2026-04-02",
        "summary": {
            "input_path": input_path,
            "finding_count": len(findings),
            "severity_counts": {"warning": len(findings)},
            "status": "issues_found" if findings else "clean",
        },
        "findings": findings,
        "artifacts": {},
    }


class DirectiveParsingTests(unittest.TestCase):
    def test_line_comment_covers_next_line(self):
        text = "// shexli-ci: EGO-I-004 - needed\ncode\n"
        covered, malformed = check_shexli._parse_directives(text)
        self.assertEqual(malformed, [])
        self.assertEqual(covered, {2: [(["EGO-I-004"], "needed")]})

    def test_block_comment_covers_line_after_its_end(self):
        text = "/* shexli-ci: EGO-I-004 - needed */\ncode\n"
        covered, _ = check_shexli._parse_directives(text)
        self.assertEqual(covered, {2: [(["EGO-I-004"], "needed")]})

    def test_multiline_block_comment_covers_line_after_close(self):
        text = "/* shexli-ci:\n   EGO-I-004 - needed */\ncode\n"
        covered, malformed = check_shexli._parse_directives(text)
        self.assertEqual(malformed, [])
        self.assertEqual(covered, {3: [(["EGO-I-004"], "needed")]})

    def test_inline_block_comment_with_code_before_covers_current_line(self):
        text = "function f() { /* shexli-ci: EGO-I-004 - needed */\ncode\n"
        covered, _ = check_shexli._parse_directives(text)
        self.assertEqual(covered, {1: [(["EGO-I-004"], "needed")]})

    def test_block_comment_with_code_after_covers_current_line(self):
        text = "/* shexli-ci: EGO-I-004 - needed */ code;\nmore;\n"
        covered, _ = check_shexli._parse_directives(text)
        self.assertEqual(covered, {1: [(["EGO-I-004"], "needed")]})

    def test_trailing_line_comment_covers_current_line(self):
        text = "code; // shexli-ci: EGO-I-004 - needed\nnext;\n"
        covered, _ = check_shexli._parse_directives(text)
        self.assertEqual(covered, {1: [(["EGO-I-004"], "needed")]})

    def test_blank_lines_are_skipped(self):
        text = "// shexli-ci: EGO-I-004 - needed\n\n\ncode;\n"
        covered, _ = check_shexli._parse_directives(text)
        self.assertEqual(covered, {4: [(["EGO-I-004"], "needed")]})

    def test_comment_only_lines_are_skipped(self):
        text = "// shexli-ci: EGO-I-004 - needed\n// just a note\ncode;\n"
        covered, _ = check_shexli._parse_directives(text)
        self.assertEqual(covered, {3: [(["EGO-I-004"], "needed")]})

    def test_directive_without_following_code_covers_nothing(self):
        text = "// shexli-ci: EGO-I-004 - needed\n"
        covered, malformed = check_shexli._parse_directives(text)
        self.assertEqual(covered, {})
        self.assertEqual(malformed, [])

    def test_missing_rationale_is_malformed(self):
        text = "// shexli-ci: EGO-I-004\ncode\n"
        covered, malformed = check_shexli._parse_directives(text)
        self.assertEqual(covered, {})
        self.assertEqual(malformed, [(1, "EGO-I-004")])

    def test_empty_rationale_is_malformed(self):
        text = "// shexli-ci: EGO-I-004 -\ncode\n"
        covered, malformed = check_shexli._parse_directives(text)
        self.assertEqual(covered, {})
        self.assertEqual(malformed, [(1, "EGO-I-004 -")])

    def test_directive_inside_string_is_ignored(self):
        text = 'const s = "// shexli-ci: EGO-I-004 - fake";\ncode\n'
        covered, malformed = check_shexli._parse_directives(text)
        self.assertEqual(covered, {})
        self.assertEqual(malformed, [])

    def test_multiple_rule_ids(self):
        text = "// shexli-ci: EGO-I-004, EGO-P-007 - needed\ncode\n"
        covered, _ = check_shexli._parse_directives(text)
        self.assertEqual(
            covered, {2: [(["EGO-I-004", "EGO-P-007"], "needed")]})


class OccurrenceTests(unittest.TestCase):
    def test_occurrences_collapse_by_location_keeping_longest_snippet(self):
        finding = {
            "rule_id": "EGO-L-001",
            "evidence": [
                {"path": "a.js", "line": 1, "snippet": "short"},
                {"path": "a.js", "line": 1, "snippet": "a much longer snippet"},
                {"path": "a.js", "line": 2, "snippet": "other"},
            ],
        }
        occurrences = list(check_shexli._unique_occurrences(finding))
        self.assertEqual(len(occurrences), 2)
        self.assertEqual(occurrences[0][0]["snippet"], "a much longer snippet")
        self.assertEqual(occurrences[1][0]["snippet"], "other")


class CheckCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.source = os.path.join(self.dir, "src.js")
        self.report_path = os.path.join(self.dir, "report.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text, findings, input_path=None):
        with open(self.source, "w") as f:
            f.write(text)
        payload = report(findings, input_path or self.dir)
        with open(self.report_path, "w") as f:
            json.dump(payload, f)

    def run_check(self, *extra):
        env = dict(os.environ)
        env.pop("GITHUB_ACTIONS", None)
        return subprocess.run(
            [sys.executable, CHECK, "--source", self.dir,
             self.report_path, *extra],
            capture_output=True, text=True, env=env)

    def test_acknowledged_finding_does_not_block(self):
        self.write(
            "// shexli-ci: EGO-I-004 - needed\nimports._gi;\n",
            [finding("EGO-I-004", self.source, [2])])
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Acknowledged inline, non-blocking", result.stdout)
        self.assertIn("acknowledged: needed", result.stdout)

    def test_unacknowledged_finding_blocks(self):
        self.write("imports._gi;\n",
                   [finding("EGO-I-004", self.source, [1])])
        result = self.run_check()
        self.assertEqual(result.returncode, 1)

    def test_blank_lines_before_the_next_code_line_are_skipped(self):
        self.write(
            "// shexli-ci: EGO-I-004 - needed\n\nimports._gi;\n",
            [finding("EGO-I-004", self.source, [3])])
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_trailing_line_comment_acknowledges_same_line(self):
        self.write(
            "imports._gi; // shexli-ci: EGO-I-004 - needed\n",
            [finding("EGO-I-004", self.source, [1])])
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("acknowledged: needed", result.stdout)

    def test_trailing_block_comment_acknowledges_same_line(self):
        self.write(
            "imports._gi; /* shexli-ci: EGO-I-004 - needed */\n",
            [finding("EGO-I-004", self.source, [1])])
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("acknowledged: needed", result.stdout)

    def test_only_matching_rule_is_acknowledged(self):
        self.write(
            "// shexli-ci: EGO-P-007 - needed\nimports._gi;\n",
            [finding("EGO-I-004", self.source, [2])])
        result = self.run_check()
        self.assertEqual(result.returncode, 1)

    def test_partial_acknowledgement_keeps_blocking(self):
        self.write(
            "// shexli-ci: EGO-I-004 - first\nimports._gi;\nimports._gi;\n",
            [finding("EGO-I-004", self.source, [2, 3])])
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("[acknowledged: first]", result.stdout)

    def test_missing_rationale_fails_even_with_allow_new(self):
        self.write(
            "// shexli-ci: EGO-I-004\nimports._gi;\n",
            [finding("EGO-I-004", self.source, [2])])
        result = self.run_check("--allow-new")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Malformed shexli-ci directives", result.stdout)

    def test_annotations_are_warnings_for_acknowledged(self):
        self.write(
            "// shexli-ci: EGO-I-004 - needed\nimports._gi;\n",
            [finding("EGO-I-004", self.source, [2])])
        env = dict(os.environ, GITHUB_ACTIONS="true")
        result = subprocess.run(
            [sys.executable, CHECK, "--source", self.dir, self.report_path],
            capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0)
        self.assertIn("::warning ", result.stdout)
        self.assertNotIn("::error title=EGO-I-004", result.stdout)

    def test_baseline_accepted_findings_do_not_block(self):
        self.write("imports._gi;\n",
                   [finding("EGO-I-004", self.source, [1])])
        with open(self.report_path) as f:
            key = check_shexli._finding_key(json.load(f)["findings"][0])
        baseline_path = os.path.join(self.dir, "baseline.json")
        with open(baseline_path, "w") as f:
            json.dump({"spec_version": "2026-04-02", "known": [
                {"rule_id": key[0], "paths": list(key[1]),
                 "snippets": list(key[2])}]}, f)
        result = self.run_check(baseline_path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Accepted (known) findings", result.stdout)

    def test_directives_are_not_scanned_in_non_source_files(self):
        # A malformed directive in a non-JS file must not be reported/failed.
        data = os.path.join(self.dir, "data.json")
        with open(data, "w") as f:
            f.write('// shexli-ci: EGO-I-004\n')
        payload = report([finding("EGO-I-004", data, [1])], self.dir)
        with open(self.report_path, "w") as f:
            json.dump(payload, f)
        result = self.run_check()
        self.assertNotIn("Malformed shexli-ci", result.stdout)
        self.assertEqual(result.returncode, 1)

    def test_duplicate_locations_are_annotated_once(self):
        duplicate = {
            "rule_id": "EGO-L-001",
            "title": "EGO-L-001",
            "severity": "warning",
            "message": "Resource setup outside enable().",
            "evidence": [
                {"path": self.source, "line": 2, "snippet": "short"},
                {"path": self.source, "line": 2,
                 "snippet": "this._watchDog.connect('vanished', ...)"},
            ],
        }
        self.write(
            "// shexli-ci: EGO-L-001 - kept alive while disabled\n"
            "this._watchDog.connect('vanished', ...);\n",
            [duplicate])
        env = dict(os.environ, GITHUB_ACTIONS="true")
        result = subprocess.run(
            [sys.executable, CHECK, "--source", self.dir, self.report_path],
            capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("::warning title=EGO-L-001"), 1)

    def test_resolved_warns_by_default(self):
        with open(self.report_path, "w") as f:
            json.dump(report([], self.dir), f)
        baseline = os.path.join(self.dir, "baseline.json")
        with open(baseline, "w") as f:
            json.dump({"spec_version": "x", "known": [
                {"rule_id": "EGO-X-999", "paths": ["gone.js"],
                 "snippets": ["old"]}]}, f)
        result = self.run_check(baseline)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("warning: baseline entry no longer applies", result.stdout)

    def test_resolved_can_fail(self):
        with open(self.report_path, "w") as f:
            json.dump(report([], self.dir), f)
        baseline = os.path.join(self.dir, "baseline.json")
        with open(baseline, "w") as f:
            json.dump({"spec_version": "x", "known": [
                {"rule_id": "EGO-X-999", "paths": ["gone.js"],
                 "snippets": ["old"]}]}, f)
        result = self.run_check(baseline, "--on-resolved", "fail")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR: baseline entries no longer apply", result.stdout)

    def test_relative_directory_source_resolves_directives(self):
        sub = os.path.join(self.dir, "ext")
        os.makedirs(sub)
        with open(os.path.join(sub, "extension.js"), "w") as f:
            f.write("// shexli-ci: EGO-I-004 - needed\nimports._gi;\n")
        payload = report(
            [finding("EGO-I-004", os.path.join("ext", "extension.js"), [2])],
            "ext")
        with open(self.report_path, "w") as f:
            json.dump(payload, f)
        result = subprocess.run(
            [sys.executable, CHECK, "--source", "ext",
             os.path.basename(self.report_path)],
            capture_output=True, text=True, cwd=self.dir,
            env={k: v for k, v in os.environ.items()
                 if k != "GITHUB_ACTIONS"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("acknowledged: needed", result.stdout)

    def test_summary_file(self):
        self.write("// shexli-ci: EGO-I-004 - needed\nimports._gi;\n",
                   [finding("EGO-I-004", self.source, [2])])
        summary = os.path.join(self.dir, "summary.md")
        result = self.run_check("--summary", summary)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(summary) as f:
            content = f.read()
        self.assertIn("## shexli", content)
        self.assertIn("Acknowledged inline", content)

    def test_codequality_report(self):
        with open(os.path.join(self.dir, "src.js"), "w") as f:
            f.write("// shexli-ci: EGO-I-004 - needed\n"
                    "imports._gi;\nimports._gi;\n")
        payload = report([finding("EGO-I-004", "src.js", [2, 3])], ".")
        with open(self.report_path, "w") as f:
            json.dump(payload, f)
        result = subprocess.run(
            [sys.executable, CHECK, "--source", ".",
             "--codequality", "codequality.json",
             os.path.basename(self.report_path)],
            capture_output=True, text=True, cwd=self.dir,
            env={k: v for k, v in os.environ.items()
                 if k != "GITHUB_ACTIONS"})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        with open(os.path.join(self.dir, "codequality.json")) as f:
            entries = json.load(f)
        by_line = {e["location"]["lines"]["begin"]: e for e in entries}
        self.assertEqual(by_line[2]["severity"], "info")
        self.assertIn("acknowledged: needed", by_line[2]["description"])
        self.assertEqual(by_line[3]["severity"], "minor")
        self.assertEqual(by_line[3]["check_name"], "EGO-I-004")
        self.assertEqual(by_line[3]["location"]["path"], "src.js")
        self.assertTrue(by_line[3]["fingerprint"])

    def test_gitlab_section_output(self):
        self.write("// shexli-ci: EGO-I-004 - needed\nimports._gi;\n",
                   [finding("EGO-I-004", self.source, [2])])
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_ACTIONS"}
        env["GITLAB_CI"] = "true"
        result = subprocess.run(
            [sys.executable, CHECK, "--source", self.dir, self.report_path],
            capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("section_start:", result.stdout)
        self.assertIn("section_end:", result.stdout)
        self.assertNotIn("::group::", result.stdout)

    def test_zip_source_is_supported(self):
        archive = os.path.join(self.dir, "extension.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(
                "src.js",
                "// shexli-ci: EGO-I-004 - needed\nimports._gi;\n")
        payload = report(
            [finding("EGO-I-004", f"{archive}:src.js", [2])], archive)
        with open(self.report_path, "w") as f:
            json.dump(payload, f)
        result = subprocess.run(
            [sys.executable, CHECK, "--source", archive, self.report_path],
            capture_output=True, text=True,
            env={k: v for k, v in os.environ.items()
                 if k != "GITHUB_ACTIONS"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("acknowledged: needed", result.stdout)


if __name__ == "__main__":
    unittest.main()
