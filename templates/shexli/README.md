# shexli

Runs [shexli](https://gitlab.gnome.org/3v1n0/extensions-web/-/tree/51-improvements/shexli),
a static analyzer for GNOME Shell extensions, and fails when *new* findings
appear compared to a baseline of known ones. It reuses the same check script as
the [shexli-ci](https://github.com/3v1n0/shexli-ci) GitHub action.

## Usage

```yaml
include:
  - component: $CI_SERVER_FQDN/<path-to>/shexli-ci/shexli@main
    inputs:
      path: extension.zip
      baseline: shexli-baseline.json
```

The job clones the shexli-ci scripts and installs shexli with pip. The extension
package must already be built: set the `build` input to a command that produces
it, or consume an artifact from an earlier job.

## Inputs

| Input               | Default                          | Description                                             |
| ------------------- | -------------------------------- | ------------------------------------------------------- |
| `job-name`          | `shexli`                         | Name of the generated job.                              |
| `stage`             | `test`                           | Stage of the generated job.                             |
| `path`              | `.`                              | Extension directory or ZIP archive to analyze.          |
| `baseline`          | *(empty)*                        | Baseline JSON of known findings; empty is strict mode.  |
| `exclude`           | *(empty)*                        | Newline-separated glob patterns of files to skip.       |
| `on-resolved`       | `warn`                           | `warn` or `fail` when a baseline entry no longer applies.|
| `shexli-source`     | the shexli git source            | pip-installable source of shexli.                       |
| `python-version`    | `3.12`                           | Python container image tag.                             |
| `build`             | *(empty)*                        | Command run before the analysis to build the package.   |
| `report-path`       | `shexli-report.json`             | Where to write the JSON report.                         |
| `summary-path`      | `shexli-summary.md`              | Where to write the markdown summary.                    |
| `codequality-path`  | `shexli-codequality.json`        | Where to write the Code Quality report.                 |
| `shexli-ci-repo`    | the GitHub repository            | shexli-ci repository to clone; override for a mirror.   |
| `shexli-ci-ref`     | the component reference          | Ref of shexli-ci to use.                                |

## Artifacts

- `shexli-report.json` — the raw shexli JSON report.
- `shexli-summary.md` — the markdown summary.
- `shexli-codequality.json` — the Code Quality report, shown in the merge
  request **Reports** tab (and in the **Changes** view on Ultimate).

New findings fail the job. Findings acknowledged inline with a `shexli-ci:`
directive are still reported (as `info` in the Code Quality report) but do not
fail. See the [shexli-ci README](https://github.com/3v1n0/shexli-ci#acknowledging-findings-inline)
for the directive syntax.
