# RavenProject/Ravencoin v4.8.0 provenance (live GitHub API, 2026-08-20)

## Tag resolution
- repository: RavenProject/Ravencoin
- tag: v4.8.0
- tag ref type: lightweight (refs/tags/v4.8.0 object.type == "commit", no tag object)
- FULL 40-char tag target commit: 22549129888d02e0e08fcdb9f96f3c699167e774
- commit message: "Merge pull request #1289 from hans-schmidt/v4p8p0"
- parents: 408e372e742776ee07be5abf148ea4aa52c98ec6, b60f50e04f1fba425b28804e61be2694faaf3469
- tree sha (tag commit): 5c222b268c91e67d67721e32b68210003a4b6688

## Relation to previously pinned b60f50e04f1fba425b28804e61be2694faaf3469
- b60f50e commit message: "Bump version to 4.8.0"
- b60f50e tree sha: 5c222b268c91e67d67721e32b68210003a4b6688 (IDENTICAL to tag commit tree)
- compare API b60f50e...22549129888d: status=ahead, ahead_by=1, behind_by=0, files changed=0
- conclusion: tag commit is a zero-diff merge of b60f50e into develop; tree-equivalent but NOT
  the same commit identity. repository+commit stays the certification key: official candidate
  identity is RavenProject/Ravencoin@22549129888d02e0e08fcdb9f96f3c699167e774, not @b60f50e.

## Release metadata
- release name: "V4.8.0 Core Release: Patch 2 major bugs"
- html_url: https://github.com/RavenProject/Ravencoin/releases/tag/v4.8.0
- target_commitish: master

## amd64 binary artifact (unchanged by commit-identity fix: GitHub release binary asset is fixed)
- asset: raven-4.8.0-225491298-x86_64-linux-gnu.tar.gz
- byte size: 34706795
- published sha256sum file: cb359b6a5b42e47068cd655231484fcc763d2f79eae5ea318b029c704a4dc020
- independently recomputed sha256: cb359b6a5b42e47068cd655231484fcc763d2f79eae5ea318b029c704a4dc020 (MATCH)
- extracted bin/ravend sha256:    885f6670c819e3a48339bbc596f1a224fe41af21ae7a0db57b2ebca700d050ea (MATCH vs Dockerfile pin)
- extracted bin/raven-cli sha256: 8fde2465b2bd50d0db2d873fd02a6a4ce6a6be84ed03d32b094a9862febb594b (MATCH vs Dockerfile pin)
- CI build number 225491298 confirmed present in actual release asset filenames (not derivable from version alone)

## arm64 source archive (CHANGES with commit-identity fix: archive path prefix embeds commit sha)
- old (b60f50e) archive sha256: ad6e72c18b64835e74582ad2465a72564b12310fa6159f23a67b9ecaf9b5ca71 (matches current Dockerfile pin)
- new (22549129888d) archive sha256: 0fba4c8979c7ebed3457d91957f886292ea53e62857c636c70e3aa0478017d6e
- action required: Dockerfile / ci.yml RAVENCOIN_SOURCE_COMMIT and RAVENCOIN_SOURCE_ARCHIVE_SHA256
  must move to the tag-resolved commit and its own archive digest.
