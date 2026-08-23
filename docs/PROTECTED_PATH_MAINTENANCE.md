# Protected-path maintenance

## Legitimately changing a protected path

There is no in-repository bypass for the `Protected path scope` check. When a reviewed maintainer change must alter a protected path, the repository owner temporarily removes `Protected path scope` from the target branch's required checks, merges only the already-reviewed protected-path change, and immediately re-enables `Protected path scope` as a required check. No unrelated change may be included in that maintenance merge. Before normal feature PRs resume, confirm in branch-protection settings that the check is required again.
