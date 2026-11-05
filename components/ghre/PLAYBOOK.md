# Playbook: recovering a suspended GitHub account

Write down what existed before trying to get it back. That is the whole method.
Everything below is the detail.

## The situation

GitHub answers 404 for a suspended account's profile, its repositories, its API
paths, and its raw files. Nothing at the origin is retrievable. A different IP
does not help: mirrors and caches report the origin's 404, because the content
is gone rather than blocked.

What remains survives in six places, and they need six different techniques.

## Lane -1: the copy taken before any of this is needed

The lanes below are about losing as little as possible. This one is about not
losing anything, and it is the only one that runs while the account is healthy.
If a repository matters to you, snapshot it on a schedule; the rest of this
playbook is the fallback for the day nobody did.

```
ghre snapshot OWNER/REPO --out work/snap --save --poll --sweeps 8
```

Two copies, because they fail in different ways.

**The directory is authoritative.** Every issue and pull request as the API
returned it, plus a Markdown rendering holding the body, the comments, the
reviews and the review comments. `MANIFEST.json` carries a SHA-256 per file, so
a copy that lost bytes is caught by `--verify` instead of being trusted. This is
what a refiling is written from: the text is here, and nothing needs the network.

**The captures are the durable copy.** Each page goes to Save Page Now through
the archive broker, which answers a job id that is written to `SAVES.json`
before the next request leaves. A capture is keyed on the repository's own URL,
so it outlives the account that wrote the issue and the account that asked for
the copy. Two limits: a capture is a rendering of the page at that instant, so a
later edit is a later capture, and it is the directory that carries the exact
text.

Why enumerating early matters as much as storing the words: a suspended
account's issues 404 and the repository's list skips their numbers entirely, so
"which objects existed" is itself something only a snapshot can prove. The
snapshot records one row per object with its number, kind, author and dates, and
that row is the evidence a later recovery files under.

Cost: one request per object for its comments, one or two more for a pull
request, then one submission per page. Nothing is polled in a tight loop; the
broker's own ceiling paces the run, and a run that stops resumes from
`SAVES.json` without asking anything twice.

Pacing, measured: a save is the expensive kind of request. A run of a thousand
pages pushed hard trips the broker's spend breaker, which then holds the caller
to about ten saves a minute for a while; the submissions keep succeeding under
that cap, each answering a job id, but the run stops being fast. Plan for hours
on a thousand-page run, run it detached, and let `SAVES.json` carry the state:
the archive finishes its queue whether or not this host is still watching.

## Lane 0: the event feed, which survives the suspension

GitHub deletes the account's issues with the account. `repos/O/R/issues/N`
returns 404, and the repository's issue list skips that number, so the object is
gone rather than hidden. The event feed answers anyway:

```
GET https://api.github.com/users/<account>/events/public?per_page=100
```

The payload carries the object as it was when the event fired. An `IssuesEvent`
holds the entire issue, body included; an `IssueCommentEvent` holds the comment.

What it does not give: the account's own repositories never appear in it, only
repositories the account acted on; and it covers roughly 90 days, so it is not a
history.

Related endpoints, and what each answers for a suspended account:

| Endpoint | Expected answer |
|---|---|
| `users/<account>/events/public` | the feed, whole |
| `users/<account>/received_events/public` | reachable, usually empty |
| `repos/O/R/events` | last day only, and only the repository's own actors |
| `networks/O/R/events` | the fork network, same short window |
| `repos/O/R/issues/events` | label and close events, no bodies |
| `users/<account>` | 404 |
| `users/<account>/repos` | `[]` |
| `search/*?q=...<account>...` | 422, the account qualifier is refused |

## Lane 1: the ledger, from archived URL lists

The Wayback CDX API lists every URL archived under a path prefix:

```
https://web.archive.org/cdx/search/cdx?url=github.com%2F<account>%2F&matchType=prefix&output=json&limit=500&collapse=urlkey
```

Row zero is the header. Each row is
`[urlkey, timestamp, original, mimetype, statuscode, digest, length]`.

This one query is the highest-yield call in the playbook. It answers three
questions at once:

1. **Which repositories existed.** Split `original` after
   `github.com/<account>/` and take the first path segment. This finds
   repositories an archived profile never listed, because a profile shows only
   popular ones.
2. **Which of their pages were captured.** Anything with status 200 is
   retrievable.
3. **Where the issues and pull requests are.** A captured `/pull/N` or
   `/issues/N` URL is a directly recoverable page.

Then query per repository for depth, which the account-level query truncates:

```
...&url=github.com%2F<account>%2F<repo>&matchType=prefix...
```

**Do not guess URLs.** A prefix query returns what was archived; a guessed list
returns what you happened to imagine. The guessed approach misses query-string
captures like `commits?author=<account>&since=...`, which are often the most
valuable captures for a repository.

**A slow prefix query is not an empty one.** A wide prefix can take longer than
the client deadline and answer 5xx. Retry with a longer deadline before
recording a negative.

## Lane 2: archived pages, and the diff inside them

Fetch a captured page with the `id_` modifier, which returns the original bytes
instead of a rewritten copy:

```
https://web.archive.org/web/<timestamp>id_/<original-url>
```

**A captured commit page contains its entire rendered diff as JSON.** Look for:

```html
<script type="application/json" data-target="react-app.embeddedData">
```

Walk that object for any dict with both `diffLines` and `path`. Each `diffLines`
entry has a `text` field; addition lines start with `+`, which you strip. That
reconstructs files byte for byte, with no clone.

Two properties matter:

- `isTooBig: true` means GitHub did not render the file at all. The page holds
  the path and the line count and nothing else. Record that as a gap. A large
  new file added in a single commit is exactly the case that fails here, which
  is usually the most important file in the repository.
- `isBinary` and submodule entries carry no text either.

## Lane 3: pull refs, which outlive the account

The REST API hides a suspended user's pull requests. The git protocol still
serves the refs, because the destination repository owns them:

```
git ls-remote https://github.com/OWNER/REPO 'refs/pull/*'
```

Then one fetch brings in every PR head the repository ever had:

```
git clone --bare --filter=blob:none https://github.com/OWNER/REPO repo.git
cd repo.git
git fetch origin '+refs/pull/*/head:refs/pr/*'
git log --all --format='%H%x09%an <%ae>%x09%aI%x09%s'
```

Filter on the author email. A deleted account's commits keep their noreply
address, `<id>+<account>@users.noreply.github.com`, so the identity survives
even when the profile does not. `git show <sha>` gives the full patch.

**A gap in a sequence is the signal.** If `/issues/100` 404s while #97 and #98
are present, the object is there and the API is hiding it. Ask git instead.

**Target repositories the account did not own.** Its own repositories are the
ones that vanished. Everybody else's repositories it contributed to are intact
and still serve the objects.

**A forwarding broker serves git too.** A path-prefix proxy that appends the
requested URL to its own origin answers `.../info/refs?service=git-upload-pack`
with a real `application/x-git-upload-pack-advertisement`. Point `git` at
`https://<broker>/https://github.com/OWNER/REPO` and a bare clone, a ref listing
and a pull-ref fetch all behave exactly as they do against GitHub. This is how
the git lanes run on a host with no direct route out. The ref list is
byte-identical to a direct `ls-remote`, which is the check worth making before
trusting one.

**Sweep branches as well as pull refs.** A repository with no pull refs yields
nothing to a pull-only sweep, and reports clean. One measured run had 285 of 926
repositories with no pull ref at all; sweeping their plain branches found a
third of the commits.

## Lane 4: commits by the author, in live repositories

The commit search accepts `author:<login>` and returns commits inside
repositories that still exist:

```
https://api.github.com/search/commits?q=author%3A<account>&per_page=50
```

Per-repository listing is more reliable than search, which can report
`incomplete_results: true`:

```
https://api.github.com/repos/OWNER/REPO/commits?author=<account>&per_page=100
```

**An unknown account in an `author:` qualifier makes GitHub answer 422, not an
empty list.** Treating that as "no commits" is wrong. It means the account name
resolves to nothing, so the qualifier is refused.

Note also that commit search returns the repository in `repository.full_name`
while issue search returns `repository_url`. Reading only one silently loses
half the hits.

## Lane 5: search snippets, the prose of last resort

A deleted page's text sometimes survives in an index that is still queryable.
Each result is a window: a title plus the opening of a body. Keep every hit with
the query that produced it, so a reader can see how it was found and can widen
the search from the recovered text itself.

Snippets are windows, not files. They prove what a document said while giving
you the document's shape and voice, and nothing more.

## Failure classes, and what each one means

| Answer | Meaning | Action |
|---|---|---|
| 404 at the origin | the thing is gone from the origin | move to an archive lane; do not retry |
| 404 from a mirror | the mirror reports the origin's answer | a real negative for the mirror, not evidence about the object |
| empty JSON array | the index has nothing for this URL | a real negative |
| 5xx or timeout | the index was not read | a gap, not a negative; retry with a longer deadline |
| 429 | a hold or a spend breaker | back off, retry twice, then record |
| 422 on issue search | query missing `is:issue` or `is:pull-request` | fix the query |
| 422 on `author:` | the account does not resolve | gap, not a negative |
| 403 with a suspension message | the token belongs to a suspended account | drop the token and continue unauthenticated |

Never bypass a captcha. A captcha is a guard working, not an obstacle to route
around.

## Ordering, and why

1. **Ledger.** Cheap, safe, and it decides everything downstream. A recovery
   run without a ledger cannot tell a gap from a negative.
2. **Events.** One request per hundred events, and it is the only copy of any
   issue or comment the account opened on somebody else's repository.
3. **Refs and objects.** Highest yield for code: whole commits with patches.
4. **Archive.** Reconstructs files from captured commit pages.
5. **Snippets.** Last, because it costs the most requests for the least text.

## Discipline

- **Observed and inferred never merge.** "The page was captured at 08:02:33
  with status 200" is observed. "The report was probably long" is inferred, and
  needs its own section.
- **A number carries its conditions.** Which host, which tools, which day, how
  many runs. A count of commits is a floor when the search said
  `incomplete_results`.
- **An empty result is a result.** Say what was swept and what would have had to
  be true for the sweep to find something.
- **Never widen a claim to cover a gap.** "No capture was found" and "the index
  was never read" are different sentences and both belong in the report.
- **Name your own repositories yourself.** A repository the account owned is
  gone from every index. A seed file with a list of names is the only route, and
  the ledger labels those rows as supplied by a person rather than crawled, so a
  reader knows the difference.
- **Re-derive, do not trust.** A previous note that a capture exists is a claim.
  A replay URL that returns 200 with the body is evidence.

## Extending it

Every endpoint this tool uses is a public one: the GitHub REST API and the
Internet Archive's CDX and replay endpoints. Add a lane by adding a function
that returns rows, and record what it could not get as a gap rather than as a
negative.

Two transports are supported and either serves every lane, git included. A
deployment that must leave through one host does not need a code change: choose
the forwarding transport, whose clone URL is `<broker>/https://github.com/...`
and whose API base is the broker's forge path.
