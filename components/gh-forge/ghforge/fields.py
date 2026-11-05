"""The exact `--json` field sets real `gh` accepts, and field selection.

Harvested from `gh <cmd> --json __nope__`, which is how gh itself publishes the
list, so an unknown field here fails with the same message and the same
alphabetical list.
"""

from __future__ import annotations

from .errors import UsageError

FIELD_SETS = {
    'repo': [
        'archivedAt', 'assignableUsers', 'codeOfConduct', 'contactLinks', 'createdAt', 'defaultBranchRef',
        'deleteBranchOnMerge', 'description', 'diskUsage', 'forkCount', 'fundingLinks', 'hasDiscussionsEnabled',
        'hasIssuesEnabled', 'hasProjectsEnabled', 'hasWikiEnabled', 'homepageUrl', 'id', 'isArchived',
        'isBlankIssuesEnabled', 'isEmpty', 'isFork', 'isInOrganization', 'isMirror', 'isPrivate',
        'isSecurityPolicyEnabled', 'isTemplate', 'isUserConfigurationRepository', 'issueTemplates',
        'issues', 'labels', 'languages', 'latestRelease', 'licenseInfo', 'mentionableUsers', 'mergeCommitAllowed',
        'milestones', 'mirrorUrl', 'name', 'nameWithOwner', 'openGraphImageUrl', 'owner', 'parent',
        'primaryLanguage', 'projects', 'projectsV2', 'pullRequestTemplates', 'pullRequests', 'pushedAt',
        'rebaseMergeAllowed', 'repositoryTopics', 'securityPolicyUrl', 'squashMergeAllowed', 'sshUrl',
        'stargazerCount', 'templateRepository', 'updatedAt', 'url', 'usesCustomOpenGraphImage',
        'viewerCanAdminister', 'viewerDefaultCommitEmail', 'viewerDefaultMergeMethod', 'viewerHasStarred',
        'viewerPermission', 'viewerPossibleCommitEmails', 'viewerSubscription', 'visibility', 'watchers',
    ],
    'issue': [
        'assignees', 'author', 'blockedBy', 'blocking', 'body', 'closed', 'closedAt', 'closedByPullRequestsReferences',
        'comments', 'createdAt', 'id', 'isPinned', 'issueType', 'labels', 'milestone', 'number',
        'parent', 'projectCards', 'projectItems', 'reactionGroups', 'state', 'stateReason', 'subIssues',
        'subIssuesSummary', 'title', 'updatedAt', 'url',
    ],
    'pr': [
        'additions', 'assignees', 'author', 'autoMergeRequest', 'baseRefName', 'baseRefOid', 'body',
        'changedFiles', 'closed', 'closedAt', 'closingIssuesReferences', 'comments', 'commits',
        'createdAt', 'deletions', 'files', 'fullDatabaseId', 'headRefName', 'headRefOid', 'headRepository',
        'headRepositoryOwner', 'id', 'isCrossRepository', 'isDraft', 'labels', 'latestReviews',
        'maintainerCanModify', 'mergeCommit', 'mergeStateStatus', 'mergeable', 'mergedAt', 'mergedBy',
        'milestone', 'number', 'potentialMergeCommit', 'projectCards', 'projectItems', 'reactionGroups',
        'reviewDecision', 'reviewRequests', 'reviews', 'state', 'statusCheckRollup', 'title', 'updatedAt',
        'url',
    ],
    'release_list': [
        'createdAt', 'isDraft', 'isImmutable', 'isLatest', 'isPrerelease', 'name', 'publishedAt',
        'tagName',
    ],
    'release_view': [
        'apiUrl', 'assets', 'author', 'body', 'createdAt', 'databaseId', 'id', 'isDraft', 'isImmutable',
        'isPrerelease', 'name', 'publishedAt', 'tagName', 'tarballUrl', 'targetCommitish', 'uploadUrl',
        'url', 'zipballUrl',
    ],
    'run': [
        'attempt', 'conclusion', 'createdAt', 'databaseId', 'displayTitle', 'event', 'headBranch',
        'headSha', 'name', 'number', 'startedAt', 'status', 'updatedAt', 'url', 'workflowDatabaseId',
        'workflowName',
    ],
    'run_view': [
        'attempt', 'conclusion', 'createdAt', 'databaseId', 'displayTitle', 'event', 'headBranch',
        'headSha', 'jobs', 'name', 'number', 'startedAt', 'status', 'updatedAt', 'url', 'workflowDatabaseId',
        'workflowName',
    ],
    'workflow': [
        'id', 'name', 'path', 'state',
    ],
    'label': [
        'color', 'createdAt', 'description', 'id', 'isDefault', 'name', 'updatedAt', 'url',
    ],
    'search_repos': [
        'createdAt', 'defaultBranch', 'description', 'forksCount', 'fullName', 'hasDownloads',
        'hasIssues', 'hasPages', 'hasProjects', 'hasWiki', 'homepage', 'id', 'isArchived', 'isDisabled',
        'isFork', 'isPrivate', 'language', 'license', 'name', 'openIssuesCount', 'owner', 'pushedAt',
        'size', 'stargazersCount', 'updatedAt', 'url', 'visibility', 'watchersCount',
    ],
    'search_issues': [
        'assignees', 'author', 'authorAssociation', 'body', 'closedAt', 'commentsCount', 'createdAt',
        'id', 'isLocked', 'isPullRequest', 'labels', 'number', 'repository', 'state', 'title',
        'updatedAt', 'url',
    ],
    'search_prs': [
        'assignees', 'author', 'authorAssociation', 'body', 'closedAt', 'commentsCount', 'createdAt',
        'id', 'isDraft', 'isLocked', 'isPullRequest', 'labels', 'number', 'repository', 'state',
        'title', 'updatedAt', 'url',
    ],
    'search_code': [
        'path', 'repository', 'sha', 'textMatches', 'url',
    ],
    'search_commits': [
        'author', 'commit', 'committer', 'id', 'parents', 'repository', 'sha', 'url',
    ],
}

# Which field set each command answers with.
COMMAND_FIELDS = {
    "repo view": "repo",
    "repo list": "repo",
    "issue list": "issue",
    "issue view": "issue",
    "pr list": "pr",
    "pr view": "pr",
    "release list": "release_list",
    "release view": "release_view",
    "run list": "run",
    "run view": "run_view",
    "workflow list": "workflow",
    "label list": "label",
    "search repos": "search_repos",
    "search issues": "search_issues",
    "search prs": "search_prs",
    "search code": "search_code",
    "search commits": "search_commits",
}


def available(command):
    return FIELD_SETS[COMMAND_FIELDS[command]]


def check(requested, command):
    """gh validates the whole list before it makes a request."""
    known = available(command)
    for name in requested:
        if name not in known:
            raise UsageError(
                'Unknown JSON field: "%s"\nAvailable fields:\n%s'
                % (name, "\n".join("  " + field for field in known)))
    return requested


def parse(value):
    """--json accepts `a,b` and repeats; keep the caller's order, drop dupes."""
    fields = []
    for chunk in str(value or "").split(","):
        chunk = chunk.strip()
        if chunk and chunk not in fields:
            fields.append(chunk)
    return fields


def select(obj, requested, command):
    """Project `obj` (already gh-shaped) down to the requested fields."""
    check(requested, command)
    return {name: obj.get(name) for name in requested}

