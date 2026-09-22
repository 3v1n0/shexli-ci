# shexli-ci

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
`--format`, `--report`, `--install`, `--source`, `--allow-new`).

## Inputs

| Input            | Default                                              | Description                                              |
| ---------------- | ---------------------------------------------------- | -------------------------------------------------------- |
| `path`           | `.`                                                  | Extension directory or ZIP archive to analyze.            |
| `baseline`       | *(empty)*                                            | Baseline JSON of known findings. Empty means strict mode.|
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

Run shexli on your package and turn its findings into baseline entries:

```sh
shexli --format json extension.zip > report.json
python3 - <<'PY'
import json, sys
sys.path.insert(0, 'path/to/shexli-ci')
from check_shexli import _finding_key
report = json.load(open('report.json'))
known = []
for f in report['findings']:
    rule_id, paths, snippets = _finding_key(f)
    known.append({'rule_id': rule_id, 'paths': list(paths),
                  'snippets': list(snippets)})
json.dump({'spec_version': report.get('spec_version'), 'known': known},
          open('shexli-baseline.json', 'w'), indent=2)
PY
```

## Behavior

- Findings **not** present in the baseline make the action fail.
- Baseline entries that no longer appear are reported as *resolved* (so the
  baseline can be pruned), but do not fail.
- With no baseline, every finding fails (strict mode).

## License

GPL-3.0-or-later. See [COPYING](COPYING).
