# gh-forge

A drop-in `gh` that prefers the real GitHub CLI, and answers read commands
through [forge.web.pkgforge.dev](https://forge.web.pkgforge.dev/llms.txt) when
there is no usable token: not installed, not authenticated, expired, revoked,
suspended, or out of quota.

The point is that the caller should not have to know. Same argv, same stdout,
same exit codes, same `--json` / `--jq` / `--template` shapes. Only reads: the
proxy serves `GET /github/<rest path>` and nothing else.

```sh
# put the wrapper in front of the real gh (or: sh install.sh)
ln -s "$PWD/bin/gh" ~/.local/bin/gh      # ~/.local/bin must come first in PATH

gh issue list -R pkgforge/soar -L 3      # real gh if it works, forge otherwise
gh api repos/pkgforge/soar --jq .name
gh search repos --owner pkgforge --limit 5 --json fullName,stargazersCount
```

The tree is standalone and relocatable: pure Python 3.9+ standard library, no
build step, no dependencies; `bin/gh` locates the package beside itself (a
symlink into `~/.local/bin` works), the only state it writes is a mode cache
under `$XDG_CACHE_HOME/gh-forge`.

## How the mode is chosen

`GH_FORGE_MODE` is `auto` by default:

1. `gh` on `PATH`, skipping the wrapper itself, is asked `auth status`. If that
   fails, or there is no `gh` at all, the forge answers.
2. If it succeeds, one `api rate_limit` call confirms the token still works.
   A 401, a suspended account, an SSO wall or an exhausted quota switches to
   the forge.
3. The decision is cached for five minutes in `$XDG_CACHE_HOME/gh-forge`, keyed
   on the gh config's mtime, so a login or logout invalidates it immediately.
4. If the real gh is chosen but dies mid-flight with an auth-shaped error, the
   same command is retried through the forge.

Force it with `GH_FORGE_MODE=real` or `GH_FORGE_MODE=forge`. `GH_FORGE_DEBUG=1`
prints the decision and every request. `GH_FORGE_BASE` points at another
instance of the service.

In `real` mode the wrapper `exec`s the real gh with inherited stdio, so colour,
paging and progress stay exactly as they were. Nothing is re-rendered.

## What the forge mode answers

| command | REST route behind it |
| --- | --- |
| `gh api <path>` (GET) | `/github/<path>`, `Link` paging, `--paginate`, `--slurp`, `--cache`, `-i`, `-H`, `-f/-F`, `{owner}/{repo}` placeholders |
| `gh repo view` / `list` | `repos/{o}/{r}`, `users/{o}`, `orgs/{o}/repos` |
| `gh issue list` / `view` | `repos/{o}/{r}/issues`, `search/issues`, `.../comments` |
| `gh pr list` / `view` / `diff` | `repos/{o}/{r}/pulls`, `.../files`, `.../reviews`, `.../check-runs` |
| `gh release list` / `view` | `repos/{o}/{r}/releases`, `.../releases/latest` |
| `gh run list` / `view` | `repos/{o}/{r}/actions/runs`, `.../runs/{id}/jobs` |
| `gh workflow list` / `view` | `repos/{o}/{r}/actions/workflows` |
| `gh label list` | `repos/{o}/{r}/labels` |
| `gh search repos/issues/prs/code/commits` | `search/*` with gh's flag-to-qualifier mapping |
| `gh auth status` | reports which layer is answering |

Everything else is refused with a plain explanation. Writes are never faked:
`-X POST`, `gh issue create`, `gh pr merge` and friends exit 1 saying the
read-only proxy cannot carry them. `gh api graphql` is attempted, then reported
honestly when the service has no keyed account for it (the usual 503).

## Faithfulness, field by field

gh builds its `--json` output from GraphQL; the proxy only offers REST. Each
shape in `ghforge/shape.py` maps one to the other, and the differential test
(`tests/compare.py`) runs the same argv through `gh` and the wrapper and
compares bytes.

Verified identical: list/table text output (tabs, column order, state casing,
label joining), `--json` key sorting, the `Unknown JSON field` error and its
field list, `gh api` bodies and error text (`gh: Not Found (HTTP 404)`, body
without a trailing newline), paging and `Link` handling.

Known, honest differences — all of them places where the REST answer genuinely
carries something other than the GraphQL one:

- `pr list`/`pr view` `updatedAt`: GraphQL's `updatedAt` is not REST's
  `updated_at`; the REST value moves on events GraphQL does not count.
- `label list` default order: gh asks GraphQL for creation order; REST returns
  its own. `--sort name` matches exactly.
- `repo view --json repositoryTopics`: REST returns topics alphabetically,
  GraphQL in its own ordering.
- `gh api` bodies for authenticated reads can include fields that only exist
  for the proxy's account (`temp_clone_token`). The bytes are the proxy's
  answer, passed through untouched.
- Viewer-scoped fields (`viewerHasStarred`, `viewerPermission`, ...) are `null`:
  there is no viewer.
- `isPinned`, `reactionGroups`, `subIssues`, `projectItems` are empty: REST
  does not expose them. They are never guessed at.
- GraphQL-only commands (`gh discussion`, `gh project`) are not proxied.

## Reading the service's own failures

The service answers RFC 9457 problems with a `reason`. The wrapper follows its
published retry contract:

- `429 held`, `503 disabled`, `429 rate-limited`: wait `Retry-After`, then retry.
- `502 upstream-failed`/`upstream-unreachable`, `504 deadline`: exponential
  backoff, three attempts.
- `400 own-account`: a read about the service's own key; use `/users/<login>`.
- `503 none-configured`: no account configured for that route; not retryable.

A `Retry-After` longer than 20 s is not slept through: the wrapper exits 1 and
says when to come back, so an agent's loop is not blocked.

## Layout

```
bin/gh              entry point (python3)
install.sh          symlink bin/gh into ~/.local/bin (or undo that)
ghforge/cli.py      mode decision, dispatch, help
ghforge/modes.py    real gh detection, ban/expiry markers, runtime fallback
ghforge/http.py     proxy client: retries, Link paging, cache, problems
ghforge/backend.py  data, error phrasing, list/search helpers
ghforge/shape.py    REST -> the JSON shapes gh prints
ghforge/fields.py   the exact --json field sets and gh's unknown-field error
ghforge/output.py   JSON emission, --jq, --template, --include, tables
ghforge/jqish.py    jq subset (no jq binary needed)
ghforge/gotmpl.py   Go text/template subset
ghforge/cmds/*.py   one module per command family
tests/compare.py    differential test against the real gh
(tests/unit/ was mentioned in 1.0 docs but was never in the shipped tree)
```

## Tests

```sh
python3 tests/test_jq_oracle.py      # jq engine vs real gh (159 expressions)
python3 tests/test_tmpl_oracle.py    # template engine vs real gh (75 expressions)
python3 tests/test_search_oracle.py  # search commands vs real gh (24 cases)
python3 tests/compare.py             # whole-CLI differential (53 cases)
```

All four are differential: they run the same argv through the real gh and the
wrapper and compare bytes, exit codes and stderr. They need an authenticated
`gh` on the machine (as the oracle) and network access; nothing else.
