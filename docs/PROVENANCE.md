# Provenance

| part | origin | state here |
| --- | --- | --- |
| `components/gh-forge` | gh-forge.zip (gh-forge) | unchanged code. One doc line fixed: the layout list named `tests/unit/`, which the zip never contained. |
| `components/ghre` | ghre-standalone-1.2.1.zip | unchanged. 28 offline tests pass here. |
| `components/bulk-archive` | bulk-archive-kit.zip | `bulk-archive.sh` fixed: for `-f -` the temp-file cleanup sat after `exit 0` and never ran, and the stdin copy used `A && B && C || D`. It now uses `mktemp` and a trap. |
| `reference/anonymous-git-hosting` | anonymous-git-hosting.zip | unchanged. Its `socks-relay.py` is linked on PATH as `socks-relay`. |
| `reference/cfrs-field-report` | cfrs-field-report.zip and .md | unchanged. Its advice is applied in `libexec/common.sh` (pinned `CFRS_STATE_DIR`) and `libexec/publish`. |
| `install`, `bin/`, `libexec/`, `pins/`, `tests/`, `skill/`, `docs/` | new | this layer |

No feature or fallback from the five inputs was removed. Every original file is present at the paths above.
