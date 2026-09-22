#!/usr/bin/env bash
#
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

# Run shexli on a path (extension tree or ZIP archive) and compare the findings
# against a baseline of known ones. Same logic used by the GitHub action, but
# runnable locally.
#
# Usage:
#   ./run.sh [options] [PATH]
#
# Options:
#   -b, --baseline FILE   Baseline JSON of known findings (optional).
#   -x, --exclude GLOB    Exclude files matching GLOB (repeatable).
#   -f, --format FORMAT   shexli output format, json or text (default: json).
#   -o, --report FILE     Where to write the report (default: shexli-report.json).
#   -i, --install         Install/upgrade shexli before running.
#   -s, --source SPEC     pip-installable shexli source (with --install).
#       --allow-new       Do not fail on new findings, just report them.
#       --on-resolved M   Warn (default) or fail when a baseline entry no
#                         longer applies. M is "warn" or "fail".
#   -h, --help            Show this help.
#
# PATH defaults to "." and may be an extension directory or a ZIP archive.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE='git+https://gitlab.gnome.org/3v1n0/extensions-web.git@51-improvements#subdirectory=shexli'

BASELINE=''
FORMAT='json'
REPORT='shexli-report.json'
INSTALL=0
ALLOW_NEW=''
ON_RESOLVED='warn'
EXCLUDES=()

usage() { sed -n '24,39p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    -b|--baseline) BASELINE="$2"; shift 2 ;;
    -x|--exclude) EXCLUDES+=("$2"); shift 2 ;;
    -f|--format) FORMAT="$2"; shift 2 ;;
    -o|--report) REPORT="$2"; shift 2 ;;
    -i|--install) INSTALL=1; shift ;;
    -s|--source) SOURCE="$2"; shift 2 ;;
    --allow-new) ALLOW_NEW='--allow-new'; shift ;;
    --on-resolved) ON_RESOLVED="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) PATH_ARG="$1"; shift ;;
  esac
done
PATH_ARG="${PATH_ARG:-.}"

command -v shexli >/dev/null 2>&1 || {
  echo "shexli not found in PATH. Install it (e.g. with --install) or add it." >&2
  exit 2
}

if [ "$INSTALL" = 1 ]; then
  python3 -m pip install --user --upgrade "$SOURCE"
fi

args=(--format "$FORMAT")
for pattern in "${EXCLUDES[@]}"; do
  args+=(--exclude "$pattern")
done

if [ -n "$BASELINE" ] && [ "$FORMAT" != json ]; then
  echo "A baseline comparison needs the json format (use -f json)." >&2
  exit 2
fi

echo "Analyzing: $PATH_ARG"
shexli "${args[@]}" "$PATH_ARG" > "$REPORT" || true

if [ -n "$BASELINE" ]; then
  python3 "$SCRIPT_DIR/check_shexli.py" $ALLOW_NEW --source "$PATH_ARG" \
    --on-resolved "$ON_RESOLVED" "$REPORT" "$BASELINE"
else
  python3 "$SCRIPT_DIR/check_shexli.py" $ALLOW_NEW --source "$PATH_ARG" \
    --on-resolved "$ON_RESOLVED" "$REPORT"
fi
