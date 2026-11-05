# ghre

Recover what is left of a GitHub account that has been suspended, deleted or
otherwise erased.

Point it at an account name. It enumerates everything the account ever exposed,
records each item in a ledger with the source that proved it existed, and only
then tries to recover the content. The two phases are separate on purpose: a
failed recovery must never erase the record that something existed.

## Why it is built this way

When GitHub suspends an account it answers 404 for the profile, every
repository, and every API path under them. The content is gone from the origin.
What remains is scattered, and each piece needs a different technique.

| Survivor | Where it lives | What it yields |
|---|---|---|
| Event feed | `users/<account>/events/public` | whole issue and comment bodies, as they were when the event fired |
| Pull-request refs | `refs/pull/N/head` in the destination repository | the whole commit, even though the API 404s the pull request |
| Commit objects | any repository whose object store still reaches them | full patches, author identity included |
| Archived pages | the Internet Archive | rendered commit diffs, issue bodies, profile listings |
| Repository names | archived URLs under the account path | repositories a profile never displayed |
| Merged work | other people's live repositories | commits authored by the account, still served |

Each of those is a lane. They run in that order because that is the order of
expected yield.

## Install

Python 3.9 or newer. No dependencies outside the standard library. `git` must
be on `PATH` for the refs lane.

```
git clone <this repository>
cd ghre
python3 bin/ghre --version
python3 -m unittest discover -s tests -v
```

## Use

### 1. Build the ledger

```
python3 bin/ghre ledger <account> --out work/<account>
```

Writes `work/<account>/ledger.json`, `LEDGER.md`, and `requests.tsv`. Makes no
attempt to fetch content.

Six sources are read, and each row records which one produced it:

- the live API, if the account still answers;
- the event feed, which answers even when the profile 404s and carries whole
  issue and comment bodies;
- a Wayback CDX prefix query over `github.com/<account>/`, which reveals
  repository names from archived URLs;
- an archived profile page, which lists popular repositories;
- the commit and issue search, for work inside other people's repositories;
- a seed file, for names a person already knows.

### 2. Recover

```
python3 bin/ghre recover work/<account>
```

| Lane | What it does | Disable with |
|---|---|---|
| refs | clones each live repository the ledger names, fetches pull refs and branches, keeps commits whose author matches | `--skip-refs` |
| archive | CDX per repository, then fetch bodies and reconstruct commit diffs | `--skip-archive` |
| snippets | GitHub search, kept with the query that produced each hit | `--skip-snippets` |

`--sha` adds a commit to chase by object id. `--repo owner/name` adds a
repository to the refs targets. `--budget` caps HTTP requests.

### 3. Read

```
python3 bin/ghre report work/<account> --stdout
```

### 4. Export

```
bin/export-records work/<account> archives/<account> --account <account>
```

One zip per repository, holding that repository's issues, comments and
commits, and no repository content. `INDEX.md` maps every archive to its full
`owner/repo`.

## Snapshot a repository before it is lost

Every lane above reconstructs work after the fact. One lane runs the other way
round, and it is the one that would have saved the weekend: take the copy while
the objects still exist.

```
python3 bin/ghre snapshot OWNER/REPO --out work/snap --zip work/snap.zip
```

That writes, for every issue and pull request:

- `issues/<n>.json`, `prs/<n>.json`: the API's own answer, unedited;
- `<n>.md` beside each: the body, the comments, the reviews and the two kinds
  of pull-request review comment, as readable text;
- `MANIFEST.json`: a SHA-256 per rendering, so a damaged copy is detected
  rather than trusted;
- `INDEX.md` and `RECOVER.md`: what this is, and how to refile from it.

Add `--save` to ask an independent archive for a copy of every page as well:

```
python3 bin/ghre snapshot OWNER/REPO --out work/snap --save --poll --sweeps 8
```

Each page is submitted to Save Page Now through the archive broker, which
answers a job id; `--poll` follows those jobs and records the capture URL and
timestamp into `MANIFEST.json` and `SAVES.json`. This is the part that survives
the account, because a capture is keyed on the repository's own URL, not on who
asked for it.

Why both:

| Product | Holds | Survives |
|---|---|---|
| the directory | every body and comment, exactly | this disk |
| the captures | a rendering of each page | the account, and this disk |

Both are resumable. `SAVES.json` is the queue, written atomically as
submissions are accepted, so a run that stops is continued by the next one and
nothing is asked twice. `--workers 4` keeps four submissions in flight; a
submission is a round trip the broker answers in seconds, so pooling is what
turns a run of hours into one of minutes.

Cost and pacing, from a measured run rather than the docs: the broker's stated
ceiling is 100 requests a minute per caller IP, and it holds for reads. A
`/save` costs more than a read, and pushing the save lane hard, as a run of a
thousand pages does, trips a spend breaker that then caps the caller at about
ten saves a minute for a while. The submissions still succeed under that cap —
each answer carries a job id, and the archive works through its queue whether
or not anybody is polling — but a run of a thousand pages is a run of about two
hours once the breaker holds, not the fifteen minutes the raw request rate
suggests. Ask for fewer pages, or let it grind detached; the queue file makes
either safe.

To check an archive later:

```
python3 bin/ghre snapshot OWNER/REPO --out work/snap --verify
```

A submission run is long and mostly waiting, so run it detached:

```
setsid nohup python3 bin/ghre snapshot OWNER/REPO --out work/snap \
  --save --poll --sweeps 40 > snap.log 2>&1 &
```

## Configuration

| Variable | Meaning |
|---|---|
| `GHRE_TRANSPORT` | `pkgforge` (default) or `direct`. See below. |
| `GITHUB_TOKEN`, then `GH_TOKEN` | optional, `direct` only. Unauthenticated GitHub allows 60 requests an hour, a token raises it to 5000. A rejected token is dropped for the rest of the run rather than spent on every request. |

Both can also be passed as `--transport` and `--token`.

### Transports

`pkgforge` sends every request, including git, through a public forwarding
broker. Nothing leaves for GitHub or the Internet Archive directly, which means
a host that cannot reach either can still run the tool, and no token is needed.

`direct` talks to `api.github.com`, `github.com` and `web.archive.org` itself.

Git works under both. Under `pkgforge` the clone URL is the broker's forwarding
path, so `git fetch`, `git ls-remote` and a bare clone all work unchanged and
return the same refs as a direct clone:

```
git ls-remote https://<broker>/https://github.com/<owner>/<repo>
```

The broker's own limit is about 100 requests a minute per address, and a git
operation costs one or two, so a bulk sweep is fine. A `429` from it is
transient: git reports `RPC failed; HTTP 429` and the same command succeeds
after a pause.

## Output layout

```
work/<account>/
  ledger.json          machine-readable ledger, the source of truth
  LEDGER.md            the same, as a document
  requests.tsv         every request, with status and lane
  events/events.json   the raw event feed, unfiltered
  events/<owner>__<repo>/issue-N.md
                       issue bodies recovered from event payloads
  events/<owner>__<repo>/comment-<id>.md
                       comment bodies recovered from event payloads
  refs/<owner>__<repo>/patches/<sha>.patch
                       recovered commits, full diffs
  archive/<page>.html  archived pages
  archive/<page>.files/
                       files reconstructed from a captured commit diff
  snippets/snippets.md search hits, each with the query that found it

work/snap/               a snapshot, from `ghre snapshot`
  INDEX.md               every object, one line each
  RECOVER.md             how to refile from this directory
  MANIFEST.json          per-object SHA-256, plus capture state
  SAVES.json             the save queue: job ids and answers
  repo.json              the repository's own record
  issues/<n>.md          one issue, body and comments
  issues/<n>.json        the same object as the API returned it
  prs/<n>.md             one pull request, with reviews
  prs/<n>.json           the same object as the API returned it
```

## The technique that survives a suspension

GitHub deletes an account's issues along with the account. An issue the
suspended account opened answers 404 from the repository API, and the
repository's own issue list skips that number entirely, so the object is gone
rather than hidden.

The event feed still answers. It carries the object as it existed when the event
fired, body included:

```
GET https://api.github.com/users/<account>/events/public?per_page=100
```

One request can return a hundred events, and each `IssuesEvent` payload holds
the complete issue.

Two limits are worth stating. The feed covers roughly 90 days, so it is not a
history. And the account's own repositories do not appear in it at all, because
a repository the account owned went with the account; use `--seed-file` to name
them.

## The two techniques worth knowing for code

**Pull refs outlive the account.** GitHub hides a suspended user's pull
requests from the API, but the destination repository keeps
`refs/pull/N/head`. One fetch per repository retrieves every PR head it ever
had, and the author line still carries the account's id:

```
git clone --bare --filter=blob:none https://github.com/OWNER/REPO repo.git
cd repo.git && git fetch origin '+refs/pull/*/head:refs/pr/*'
git log --all --format='%H %an <%ae> %s' | grep -i '<account>'
```

A pull-ref-only sweep reads nothing from a repository that has no pull refs, so
plain branches are swept too. On one run that found a third of the commits.

**A captured commit page contains the whole diff.** GitHub embeds the rendered
diff as JSON in `<script data-target="react-app.embeddedData">`. Parsing it
reconstructs every file the page rendered. Entries flagged `isTooBig` are not
in the page at all, so they are recorded as gaps rather than written as empty
files.

## Honest limits

- Files above GitHub's diff-rendering threshold are never in a captured commit
  page. Only their names and line counts survive.
- A repository the account owned, that was never archived, and that no one
  forked is unrecoverable. The ledger records that it existed; nothing can
  recover its content.
- The event feed covers roughly 90 days and is capped by GitHub.
- The commit and issue search refuses an unknown account in an `author:`
  qualifier with a 422 rather than an empty result. That is recorded as a gap,
  not as a negative.
- Rate limits and timeouts are gaps. A `404` and a `429` mean different things
  and are never recorded as the same thing.

## Tests

```
python3 -m unittest discover -s tests -v
```

The tests are offline. They cover the ledger, the diff extractor, event
ingestion and the search-result filter.

## License

0BSD. See LICENSE.
