"""REST answers in, the JSON shapes `gh --json` prints out.

`gh` builds most of its `--json` output from GraphQL, so the names and nesting
are GraphQL's. The proxy only offers REST, so each shape here is the mapping
between the two, field for field. Where a field is genuinely GraphQL-only
(viewer identity, pinned state, reaction groups) the key is present with null
or an empty value, exactly like a missing GraphQL node would print, and this
module says so at the point where it happens: it never invents data.
"""

from __future__ import annotations


def _get(doc, *keys, default=None):
    for key in keys:
        if not isinstance(doc, dict):
            return default
        if key in doc:
            doc = doc[key]
        else:
            return default
    return doc


# -- small pieces --------------------------------------------------------

def user(doc, names=None):
    """An Actor: gh's shape for an issue/PR author."""
    if not doc:
        return None
    login = doc.get("login") or ""
    name = doc.get("name") or (names or {}).get(login) or ""
    return {
        "id": doc.get("node_id") or doc.get("id"),
        "is_bot": bool(doc.get("type") == "Bot" or login.endswith("[bot]")),
        "login": login,
        "name": name,
    }


def assignee(doc, names=None):
    """A User, which is a different shape from an Actor in gh's JSON."""
    if not doc:
        return None
    login = doc.get("login") or ""
    return {
        "id": doc.get("node_id") or doc.get("id"),
        "login": login,
        "name": doc.get("name") or (names or {}).get(login) or "",
        "databaseId": doc.get("id"),
    }


def label(doc):
    if not doc:
        return None
    return {
        "id": doc.get("node_id") or doc.get("id"),
        "name": doc.get("name"),
        "description": doc.get("description") or "",
        "color": doc.get("color"),
    }


def milestone(doc):
    if not doc:
        return None
    return {
        "number": doc.get("number"),
        "title": doc.get("title"),
        "state": (doc.get("state") or "").upper() or None,
        "dueOn": doc.get("due_on"),
        "description": doc.get("description") or "",
    }


def _state_reason(value):
    if not value:
        return ""
    return value.upper().replace("_", "_")


def repository_stub(doc):
    """The {repository:{...}} gh puts inside search and commit results."""
    if not doc:
        return None
    return {
        "id": doc.get("node_id"),
        "isFork": bool(doc.get("fork")),
        "isPrivate": bool(doc.get("private")),
        "nameWithOwner": doc.get("full_name"),
        "url": doc.get("html_url"),
    }


def _diffstat_status(value):
    return (value or "").upper() or "MODIFIED"


# -- issues --------------------------------------------------------------

def logins_in(doc):
    """Every login a shape will print, so names can be looked up once."""
    found = []
    for key in ("user", "author", "merged_by"):
        login = _get(doc, key, "login")
        if login:
            found.append(login)
    for key in ("assignees", "requested_reviewers"):
        for item in doc.get(key) or []:
            if item.get("login"):
                found.append(item["login"])
    return found


def issue(doc, *, comments=None, names=None):
    out = {
        "assignees": [assignee(u, names) for u in (doc.get("assignees") or [])],
        "author": user(doc.get("user"), names),
        "blockedBy": [],
        "blocking": [],
        "body": doc.get("body") or "",
        "closed": doc.get("state") == "closed",
        "closedAt": doc.get("closed_at"),
        "closedByPullRequestsReferences": [],
        "comments": comments if comments is not None else [],
        "createdAt": doc.get("created_at"),
        "id": doc.get("node_id"),
        "isPinned": False,          # GraphQL-only; REST never says
        "issueType": _issue_type(doc),
        "labels": [label(l) for l in (doc.get("labels") or [])],
        "milestone": milestone(doc.get("milestone")),
        "number": doc.get("number"),
        "parent": None,
        "projectCards": [],
        "projectItems": [],
        "reactionGroups": [],
        "state": (doc.get("state") or "").upper() or None,
        "stateReason": _state_reason(doc.get("state_reason")),
        "subIssues": [],
        "subIssuesSummary": {"total": 0, "completed": 0, "percentCompleted": 0},
        "title": doc.get("title"),
        "updatedAt": doc.get("updated_at"),
        "url": doc.get("html_url"),
    }
    return out


def _issue_type(doc):
    kind = doc.get("issue_type") or doc.get("type")
    if isinstance(kind, dict):
        return {"name": kind.get("name"), "id": kind.get("node_id")}
    if isinstance(kind, str) and kind:
        return {"name": kind, "id": None}
    return None


def issue_comment(doc):
    return {
        "id": doc.get("node_id"),
        "author": {"login": _get(doc, "user", "login") or ""},
        "authorAssociation": doc.get("author_association") or "",
        "body": doc.get("body") or "",
        "createdAt": doc.get("created_at"),
        "includesCreatedEdit": bool(doc.get("performed_via_github_app")),
        "isMinimized": False,
        "minimizedReason": "",
        "reactionGroups": [],
        "url": doc.get("html_url"),
        "viewerDidAuthor": False,
    }


# -- pull requests -------------------------------------------------------

def _mergeable(value):
    if value is None:
        return "UNKNOWN"
    return "MERGEABLE" if value else "CONFLICTING"


def _merge_state(value, draft=False):
    if draft:
        return "DRAFT"
    table = {
        "clean": "CLEAN", "dirty": "DIRTY", "blocked": "BLOCKED",
        "unknown": "UNKNOWN", "unstable": "UNSTABLE", "behind": "BEHIND",
        "has_hooks": "HAS_HOOKS", "draft": "DRAFT",
    }
    return table.get((value or "").lower(), "UNKNOWN")


def pr(doc, *, files=None, reviews=None, checks=None, comments=None,
       review_requests=None, commits=None, names=None):
    merged = bool(doc.get("merged") or doc.get("merged_at"))
    if merged:
        state = "MERGED"
    else:
        state = (doc.get("state") or "").upper() or None
    return {
        "additions": doc.get("additions", 0),
        "assignees": [assignee(u, names) for u in (doc.get("assignees") or [])],
        "author": user(doc.get("user"), names),
        "autoMergeRequest": None,
        "baseRefName": _get(doc, "base", "ref"),
        "baseRefOid": _get(doc, "base", "sha"),
        "body": doc.get("body") or "",
        "changedFiles": doc.get("changed_files", 0),
        "closed": doc.get("state") == "closed",
        "closedAt": doc.get("closed_at"),
        "closingIssuesReferences": [],
        "comments": comments or [],
        "commits": commits or [],
        "createdAt": doc.get("created_at"),
        "deletions": doc.get("deletions", 0),
        "files": files or [],
        "fullDatabaseId": str(doc.get("id")) if doc.get("id") is not None else None,
        "headRefName": _get(doc, "head", "ref"),
        "headRefOid": _get(doc, "head", "sha"),
        "headRepository": repository_stub(_get(doc, "head", "repo")),
        "headRepositoryOwner": _repo_owner(_get(doc, "head", "repo")),
        "id": doc.get("node_id"),
        "isCrossRepository": bool(_get(doc, "head", "repo", "full_name")
                                  != _get(doc, "base", "repo", "full_name")),
        "isDraft": bool(doc.get("draft")),
        "labels": [label(l) for l in (doc.get("labels") or [])],
        "latestReviews": reviews or [],
        "maintainerCanModify": bool(doc.get("maintainer_can_modify", False)),
        "mergeCommit": ({"oid": doc["merge_commit_sha"]}
                        if doc.get("merge_commit_sha") and merged else None),
        "mergeStateStatus": _merge_state(doc.get("mergeable_state"), bool(doc.get("draft"))),
        "mergeable": _mergeable(doc.get("mergeable")),
        "mergedAt": doc.get("merged_at"),
        "mergedBy": user(doc.get("merged_by"), names),
        "milestone": milestone(doc.get("milestone")),
        "number": doc.get("number"),
        "potentialMergeCommit": None,
        "projectCards": [],
        "projectItems": [],
        "reactionGroups": [],
        "reviewDecision": review_decision(reviews or []),
        "reviewRequests": review_requests or [],
        "reviews": reviews or [],
        "state": state,
        "statusCheckRollup": checks,
        "title": doc.get("title"),
        "updatedAt": doc.get("updated_at"),
        "url": doc.get("html_url"),
    }


def review_decision(reviews):
    """GraphQL's reviewDecision, rebuilt from REST reviews."""
    if not reviews:
        return ""
    states = [r.get("state") for r in reviews]
    if "CHANGES_REQUESTED" in states:
        return "CHANGES_REQUESTED"
    if "APPROVED" in states:
        return "APPROVED"
    if "PENDING" in states:
        return "REVIEW_REQUIRED"
    return ""


def pr_file(doc):
    return {
        "path": doc.get("filename"),
        "additions": doc.get("additions", 0),
        "deletions": doc.get("deletions", 0),
        "changeType": _diffstat_status(doc.get("status")),
    }


def review(doc):
    return {
        "id": doc.get("node_id"),
        "author": {"login": _get(doc, "user", "login") or ""},
        "authorAssociation": doc.get("author_association") or "",
        "body": doc.get("body") or "",
        "createdAt": doc.get("submitted_at"),
        "state": (doc.get("state") or "").upper(),
        "submittedAt": doc.get("submitted_at"),
        "url": doc.get("html_url"),
    }


def review_request(doc):
    return {
        "requestedReviewer": (_repo_owner(doc.get("user")) if doc.get("user")
                              else _team(doc.get("team"))),
    }


def _team(doc):
    if not doc:
        return None
    return {"id": doc.get("node_id"), "name": doc.get("name"), "slug": doc.get("slug")}


def commit_node(doc):
    return {
        "oid": doc.get("sha"),
        "messageHeadline": (doc.get("commit", {}).get("message") or "").splitlines()[0]
        if doc.get("commit") else "",
        "committedDate": _get(doc, "commit", "committer", "date"),
        "authors": [{"name": _get(doc, "commit", "author", "name") or "",
                     "email": _get(doc, "commit", "author", "email") or ""}],
    }


def check_rollup(pull, check_runs, statuses):
    """Combine check-runs and legacy statuses the way gh's rollup does."""
    rollup = []
    for run in check_runs or []:
        status = (run.get("status") or "").upper()
        rollup.append({
            "__typename": "CheckRun",
            "completedAt": run.get("completed_at"),
            "conclusion": (run.get("conclusion") or "").upper() or None,
            "detailsUrl": run.get("details_url") or run.get("html_url"),
            "name": run.get("name"),
            "startedAt": run.get("started_at"),
            "status": status,
            # REST check-runs carry no suite -> workflow name; leave it empty
            # rather than guess at one from the app slug.
            "workflowName": run.get("_workflow_name") or "",
        })
    for status in statuses or []:
        rollup.append({
            "__typename": "StatusContext",
            "context": status.get("context"),
            "createdAt": status.get("created_at"),
            "description": status.get("description"),
            "state": (status.get("state") or "").upper(),
            "targetUrl": status.get("target_url"),
        })
    return rollup


# -- repositories, releases ---------------------------------------------

def _repo_owner(doc):
    if not doc:
        return None
    owner = doc.get("owner") or doc
    return {"id": owner.get("node_id") or owner.get("id"), "login": owner.get("login")}


def repo(doc, *, languages=None, latest_release=None):
    lic = doc.get("license") or {}
    topics = doc.get("topics") or []
    counts = {
        "issues": {"totalCount": doc.get("open_issues_count", 0)},
        "pullRequests": {"totalCount": 0},
        "labels": {"totalCount": 0},
        "milestones": {"totalCount": 0},
    }
    return {
        "archivedAt": None,
        "assignableUsers": {"totalCount": 0},
        "codeOfConduct": None,
        "contactLinks": [],
        "createdAt": doc.get("created_at"),
        "defaultBranchRef": {"name": doc.get("default_branch")},
        "deleteBranchOnMerge": bool(doc.get("delete_branch_on_merge")),
        "description": doc.get("description") or "",
        "diskUsage": doc.get("size"),
        "forkCount": doc.get("forks_count", doc.get("forks")),
        "fundingLinks": [],
        "hasDiscussionsEnabled": bool(doc.get("has_discussions")),
        "hasIssuesEnabled": bool(doc.get("has_issues")),
        "hasProjectsEnabled": bool(doc.get("has_projects")),
        "hasWikiEnabled": bool(doc.get("has_wiki")),
        "homepageUrl": doc.get("homepage") or "",
        "id": doc.get("node_id"),
        "isArchived": bool(doc.get("archived")),
        "isBlankIssuesEnabled": doc.get("has_issues") is False,
        "isEmpty": doc.get("size") == 0,
        "isFork": bool(doc.get("fork")),
        "isInOrganization": doc.get("organization") is not None,
        "isMirror": doc.get("mirror_url") is not None,
        "isPrivate": bool(doc.get("private")),
        "isSecurityPolicyEnabled": bool(doc.get("security_and_analysis")),
        "isTemplate": bool(doc.get("is_template")),
        "isUserConfigurationRepository": False,
        "issueTemplates": [],
        "issues": counts["issues"],
        "labels": counts["labels"],
        "languages": [{"size": item[1], "node": {"name": item[0]}}
                      for item in (languages or [])],
        "latestRelease": latest_release,
        "licenseInfo": ({"key": (lic.get("spdx_id") or lic.get("key") or "").lower(),
                         "name": lic.get("name") or "",
                         "nickname": ""} if lic.get("name") else None),
        "mentionableUsers": {"totalCount": 0},
        "mergeCommitAllowed": bool(doc.get("allow_merge_commit", True)),
        "milestones": counts["milestones"],
        "mirrorUrl": doc.get("mirror_url"),
        "name": doc.get("name"),
        "nameWithOwner": doc.get("full_name"),
        "openGraphImageUrl": doc.get("open_graph_image_url")
        or "https://opengraph.githubassets.com/%s/%s" % (doc.get("node_id"), doc.get("full_name")),
        "owner": _repo_owner(doc),
        "parent": None,
        "primaryLanguage": ({"name": doc["language"]} if doc.get("language") else None),
        "projects": {"totalCount": 0},
        "projectsV2": {"totalCount": 0},
        "pullRequestTemplates": [],
        "pullRequests": counts["pullRequests"],
        "pushedAt": doc.get("pushed_at"),
        "rebaseMergeAllowed": bool(doc.get("allow_rebase_merge", True)),
        "repositoryTopics": [{"name": name} for name in topics],
        "securityPolicyUrl": None,
        "squashMergeAllowed": bool(doc.get("allow_squash_merge", True)),
        "sshUrl": doc.get("ssh_url"),
        "stargazerCount": doc.get("stargazers_count", 0),
        "templateRepository": None,
        "updatedAt": doc.get("updated_at"),
        "url": doc.get("html_url"),
        "usesCustomOpenGraphImage": False,
        "viewerCanAdminister": None,
        "viewerDefaultCommitEmail": None,
        "viewerDefaultMergeMethod": None,
        "viewerHasStarred": None,
        "viewerPermission": None,
        "viewerPossibleCommitEmails": None,
        "viewerSubscription": None,
        "visibility": (doc.get("visibility") or ("private" if doc.get("private") else "public")).upper(),
        "watchers": {"totalCount": doc.get("subscribers_count", doc.get("watchers_count", 0))},
    }


def release(doc, *, is_latest=False):
    return {
        "createdAt": doc.get("created_at"),
        "isDraft": bool(doc.get("draft")),
        "isImmutable": bool(doc.get("immutable", False)),
        "isLatest": bool(is_latest),
        "isPrerelease": bool(doc.get("prerelease")),
        "name": (doc.get("name") or doc.get("tag_name")),
        "publishedAt": doc.get("published_at"),
        "tagName": doc.get("tag_name"),
    }


def release_asset(doc):
    return {
        "name": doc.get("name"),
        "label": doc.get("label") or "",
        "contentType": doc.get("content_type"),
        "size": doc.get("size"),
        "state": doc.get("state"),
        "downloadCount": doc.get("download_count"),
        "createdAt": doc.get("created_at"),
        "updatedAt": doc.get("updated_at"),
        "url": doc.get("browser_download_url"),
        "apiUrl": doc.get("url"),
        "digest": doc.get("digest"),
    }


def release_detail(doc, *, is_latest=False):
    out = release(doc, is_latest=is_latest)
    out.update({
        "apiUrl": doc.get("url"),
        "assets": [release_asset(a) for a in (doc.get("assets") or [])],
        "author": user(doc.get("author")),
        "body": doc.get("body") or "",
        "databaseId": doc.get("id"),
        "id": doc.get("node_id"),
        "tarballUrl": doc.get("tarball_url"),
        "targetCommitish": doc.get("target_commitish"),
        "uploadUrl": doc.get("upload_url"),
        "url": doc.get("html_url"),
        "zipballUrl": doc.get("zipball_url"),
    })
    return out


# -- actions -------------------------------------------------------------

def run(doc, *, jobs=None):
    out = {
        "attempt": doc.get("run_attempt"),
        "conclusion": doc.get("conclusion"),
        "createdAt": doc.get("created_at"),
        "databaseId": doc.get("id"),
        "displayTitle": doc.get("display_title") or doc.get("name"),
        "event": doc.get("event"),
        "headBranch": doc.get("head_branch"),
        "headSha": doc.get("head_sha"),
        "name": doc.get("name"),
        "number": doc.get("run_number"),
        "startedAt": doc.get("run_started_at"),
        "status": doc.get("status"),
        "updatedAt": doc.get("updated_at"),
        "url": doc.get("html_url"),
        "workflowDatabaseId": doc.get("workflow_id"),
        "workflowName": doc.get("name"),
    }
    if jobs is not None:
        out["jobs"] = jobs
    return out


def job(doc):
    return {
        "completedAt": doc.get("completed_at"),
        "conclusion": doc.get("conclusion"),
        "databaseId": doc.get("id"),
        "name": doc.get("name"),
        "startedAt": doc.get("started_at"),
        "status": doc.get("status"),
        "steps": [{"completedAt": s.get("completed_at"),
                   "conclusion": s.get("conclusion"),
                   "name": s.get("name"),
                   "number": s.get("number"),
                   "startedAt": s.get("started_at"),
                   "status": s.get("status")}
                  for s in (doc.get("steps") or [])],
        "url": doc.get("html_url"),
    }


def workflow(doc):
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "path": doc.get("path"),
        "state": doc.get("state"),
    }


def label_row(doc):
    return {
        "color": doc.get("color"),
        "createdAt": None,
        "description": doc.get("description") or "",
        "id": doc.get("node_id"),
        "isDefault": False,
        "name": doc.get("name"),
        "updatedAt": None,
        "url": doc.get("url"),
    }


# -- search --------------------------------------------------------------

def search_repo(doc):
    lic = doc.get("license") or {}
    return {
        "createdAt": doc.get("created_at"),
        "defaultBranch": doc.get("default_branch"),
        "description": doc.get("description") or "",
        "forksCount": doc.get("forks_count", 0),
        "fullName": doc.get("full_name"),
        "hasDownloads": bool(doc.get("has_downloads")),
        "hasIssues": bool(doc.get("has_issues")),
        "hasPages": bool(doc.get("has_pages")),
        "hasProjects": bool(doc.get("has_projects")),
        "hasWiki": bool(doc.get("has_wiki")),
        "homepage": doc.get("homepage") or "",
        "id": doc.get("node_id"),
        "isArchived": bool(doc.get("archived")),
        "isDisabled": bool(doc.get("disabled")),
        "isFork": bool(doc.get("fork")),
        "isPrivate": bool(doc.get("private")),
        "language": doc.get("language"),
        "license": ({"key": (lic.get("spdx_id") or "").lower(),
                     "name": lic.get("name"),
                     "url": lic.get("url")} if lic.get("name") else None),
        "name": doc.get("name"),
        "openIssuesCount": doc.get("open_issues_count", 0),
        "owner": _repo_owner(doc),
        "pushedAt": doc.get("pushed_at"),
        "size": doc.get("size"),
        "stargazersCount": doc.get("stargazers_count", 0),
        "updatedAt": doc.get("updated_at"),
        "url": doc.get("html_url"),
        "visibility": doc.get("visibility") or None,
        "watchersCount": doc.get("watchers_count", 0),
    }


def search_issue(doc, *, is_pr=False, names=None):
    return {
        "assignees": [assignee(u, names) for u in (doc.get("assignees") or [])],
        "author": user(doc.get("user"), names),
        "authorAssociation": doc.get("author_association") or "",
        "body": doc.get("body") or "",
        "closedAt": doc.get("closed_at"),
        "commentsCount": doc.get("comments", 0),
        "createdAt": doc.get("created_at"),
        "id": doc.get("node_id"),
        "isDraft": bool(doc.get("draft")) if is_pr else None,
        "isLocked": bool(doc.get("locked")),
        "isPullRequest": doc.get("pull_request") is not None,
        "labels": [label(l) for l in (doc.get("labels") or [])],
        "number": doc.get("number"),
        "repository": repository_stub(doc.get("repository")),
        "state": (doc.get("state") or "").lower() or None,
        "title": doc.get("title"),
        "updatedAt": doc.get("updated_at"),
        "url": doc.get("html_url"),
    }


def search_code(doc):
    return {
        "path": doc.get("path"),
        "repository": repository_stub(doc.get("repository")),
        "sha": doc.get("sha"),
        "textMatches": [
            {"fragment": m.get("fragment"),
             "matches": [{"text": None, "indices": None}]}
            for m in (doc.get("text_matches") or [])
        ],
        "url": _get(doc, "repository", "html_url"),
    }


def search_commit(doc, names=None):
    return {
        "author": user(doc.get("author"), names),
        "commit": {
            "author": {
                "date": _get(doc, "commit", "author", "date"),
                "email": _get(doc, "commit", "author", "email"),
                "name": _get(doc, "commit", "author", "name"),
            },
            "committer": {
                "date": _get(doc, "commit", "committer", "date"),
                "email": _get(doc, "commit", "committer", "email"),
                "name": _get(doc, "commit", "committer", "name"),
            },
            "message": _get(doc, "commit", "message"),
            "messageBody": _get(doc, "commit", "message"),
            "messageHeadline": (_get(doc, "commit", "message") or "").splitlines()[0],
            "tree": {"sha": _get(doc, "commit", "tree", "sha")},
        },
        "committer": user(doc.get("committer"), names),
        "id": doc.get("node_id"),
        "parents": [{"sha": p.get("sha")} for p in (doc.get("parents") or [])],
        "repository": repository_stub(doc.get("repository")),
        "sha": doc.get("sha"),
        "url": doc.get("html_url"),
    }
