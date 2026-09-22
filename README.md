# shexli-ci

[![Tests](https://github.com/3v1n0/shexli-ci/actions/workflows/tests.yml/badge.svg)](https://github.com/3v1n0/shexli-ci/actions/workflows/tests.yml)

A reusable GitHub Action that runs [shexli](https://gitlab.gnome.org/3v1n0/extensions-web/-/tree/51-improvements/shexli),
a static analyzer for GNOME Shell extensions, against an extension package or
source tree, and fails when *new* findings appear compared to a baseline of
known ones.

This lets a project keep a record of the findings it has consciously accepted
(and not yet fixed) so that unrelated changes do not silently introduce new
review issues.

## Usage

### On a built package (recommended)

The ZIP archive is the input that matches what extensions.gnome.org reviews, so
it is the most representative one to check.

```yaml
jobs:
  shexli:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build extension
        run: make _build && (cd _build && zip -qr ../extension.zip .)

      - uses: 3v1n0/shexli-ci@main
        with:
          path: extension.zip
          baseline: shexli-baseline.json
          exclude: |
            *-48.js
```

### On the source tree

shexli also accepts a plain directory, so the action can analyze the checkout
directly (no build/zip step). This is handy for quick checks, but note the
results differ from the packaged ones: development and build files are seen,
and files that are only reachable through the packaged layout (e.g. vendored
copies) may be reported as unreachable. Use `exclude` for the files that are
not part of the shipped extension, and keep a separate baseline if needed.

```yaml
jobs:
  shexli:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: 3v1n0/shexli-ci@main
        with:
          path: .
          baseline: shexli-baseline.json
          exclude: |
            .eslintrc.yml
            .gitignore
            Makefile
            po/**
            schemas/gschemas.compiled
            debian/**
            node_modules/**
```

## Running locally

The same logic is available as `run.sh`, without GitHub Actions:

```sh
# Analyze a built package against a baseline
./run.sh -b shexli-baseline.json extension.zip

# Analyze the current tree, installing shexli if missing
./run.sh -i -x 'po/**' -x 'debian/**' .

# Just list the findings, do not fail on them
./run.sh --allow-new .
```

`run.sh --help` documents all the options (`--baseline`, `--exclude`,
`--format`, `--report`, `--install`, `--source`, `--allow-new`,
`--on-resolved`).

## Inputs

| Input            | Default                                              | Description                                              |
| ---------------- | ---------------------------------------------------- | -------------------------------------------------------- |
| `path`           | `.`                                                  | Extension directory or ZIP archive to analyze.            |
| `baseline`       | *(empty)*                                            | Baseline JSON of known findings. Empty means strict mode.|
| `on-resolved`    | `warn`                                               | `warn` or `fail` when a baseline entry no longer applies. |
| `exclude`        | *(empty)*                                            | Newline-separated glob patterns of files to skip.        |
| `shexli-source`  | `git+https://gitlab.gnome.org/3v1n0/extensions-web.git@51-improvements#subdirectory=shexli` | pip-installable source of shexli. |
| `python-version` | `3.12`                                               | Python version to run shexli with.                       |
| `format`         | `json`                                               | Output format passed to shexli.                          |
| `report-path`    | `shexli-report.json`                                 | Where to write the JSON report.                          |

## Outputs

| Output          | Description                              |
| --------------- | ---------------------------------------- |
| `report`        | Path to the generated JSON report.       |
| `finding-count` | Number of findings reported by shexli.   |

## Baseline format

The baseline lists the findings that are known and accepted. A finding is
identified by its rule id plus its evidence (evidence file base names and
snippets), which is stable across line changes, file moves within the package
and different input names.

```json
{
  "spec_version": "2026-04-02",
  "known": [
    {
      "rule_id": "EGO-I-004",
      "paths": ["utils.js"],
      "snippets": ["Gi.gobject_prototype_symbol", "Gi.hook_up_vfunc_symbol"]
    }
  ]
}
```

`spec_version` is informational; it records the shexli spec the baseline was
generated with.

### Generating/updating the baseline

Run shexli on your package and turn its findings into baseline entries with
`update_baseline.py`:

```sh
shexli --format json extension.zip > report.json
./update_baseline.py report.json shexli-baseline.json
```

`update_baseline.py` is self-contained, so it can be copied into a project and
run from there. By default it writes the findings of the given report as the
new baseline; pass `--merge` to keep entries that are no longer reported (for
example when generating from a subset of the package).

Generate the baseline from the same kind of input the check runs on (the built
ZIP archive), otherwise the findings will not match.

## Acknowledging findings inline

Besides the baseline, a single occurrence can be acknowledged right next to the
code. Put the directive alone on the line before it, and it acknowledges the
next code line (blank and comment-only lines in between are skipped):

```js
// shexli-ci: EGO-I-004 - required to reimplement the vfunc
prototype = prototype[Gi.hook_up_vfunc_symbol];
```

Or attach it to the offending line itself, as a trailing `//` comment or a
`/* */` comment surrounded by code:

```js
prototype = prototype[Gi.hook_up_vfunc_symbol]; // shexli-ci: EGO-I-004 - required
this._staticBox = new Clutter.ActorBox(); /* shexli-ci: EGO-L-002 - sized later */
```

So a directive acknowledges **the line it shares with code**, if any, and
otherwise **the next code line**. It must name one or more exact rule ids
(comma or space separated) and must carry a non-empty rationale after ` - `.

Acknowledged occurrences are still reported and annotated, but as **warnings**,
and they never make the check fail. Only the acknowledged occurrence is
ignored: if a finding has other occurrences without a directive, those keep
failing. A directive without a rationale is invalid, acknowledges nothing and
fails the check.

Directives are read from the analyzed input (the ZIP archive or directory), so
they must be part of the packaged extension. They are recognized in
JavaScript-style sources (`.js`, `.mjs`, `.ts`, `.css`, …).

## Behavior

- Findings **not** present in the baseline make the action fail.
- Baseline entries that no longer appear are reported as *resolved* (so the
  baseline can be pruned). By default this is a warning; set `on-resolved:
  fail` to make the check fail instead, forcing the baseline to be updated.
- Occurrences acknowledged with an inline `shexli-ci:` directive do not fail,
  but are still reported (as warnings).
- A malformed `shexli-ci:` directive (missing rationale) fails the check.
- With no baseline, every finding fails (strict mode), unless acknowledged
  inline.

## Reports

When running on GitHub Actions the action also:

- **Annotates the new findings** on their source lines (as errors/warnings in
  the *Files changed* view of the pull request), pointing at the offending file
  and line, with one annotation per occurrence. Occurrences acknowledged with an
  inline `shexli-ci:` directive are annotated as warnings and include their
  rationale.
- Writes a **job summary** with the new findings, the acknowledged ones, the
  accepted ones (collapsed) and the resolved ones, shown on the workflow run
  page.

The console output is verbose: a line for each finding and one for each of its
occurrences, with the snippet collapsed on a single line. On GitHub Actions
each occurrence is emitted as its own collapsible group (with the snippet shown
as-is), so the log stays tidy. Annotations are only produced for findings that
are not in the baseline. This does not happen when running `run.sh` locally.

## License

GPL-3.0-or-later. See [COPYING](COPYING).
