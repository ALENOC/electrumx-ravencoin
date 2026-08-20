#!/bin/sh
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.
#
# Reproducibly builds a RavenProject/Ravencoin candidate commit, plus the
# certification candidate probe suite, via
# docker/core-safety/candidate-build.Dockerfile, and extracts the resulting
# ravend/raven-cli/test_raven binaries and full source tree to a local
# directory for core-safety/scripts/certify_core.py to consume via
# --source-dir/--bin-dir/--candidate-probe/--candidate-test-binary.
#
# Usage:
#   build_candidate.sh <commit> <source-archive-sha256> [repository] [out-dir]
#
# repository defaults to RavenProject/Ravencoin. out-dir defaults to
# build/candidate/<commit> under the repository root.

set -eu

commit="${1:?usage: build_candidate.sh <commit> <source-archive-sha256> [repository] [out-dir]}"
archive_sha256="${2:?usage: build_candidate.sh <commit> <source-archive-sha256> [repository] [out-dir]}"
repository="${3:-RavenProject/Ravencoin}"

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
out_dir="${4:-$repo_root/build/candidate/$commit}"
image_tag="chainstrap-candidate-build:$commit"
container_name="chainstrap-candidate-build-$commit"

mkdir -p "$out_dir"

docker build \
    --file "$repo_root/docker/core-safety/candidate-build.Dockerfile" \
    --build-arg "RAVENCOIN_SOURCE_REPOSITORY=$repository" \
    --build-arg "RAVENCOIN_SOURCE_COMMIT=$commit" \
    --build-arg "RAVENCOIN_SOURCE_ARCHIVE_SHA256=$archive_sha256" \
    --tag "$image_tag" \
    "$repo_root"

docker rm -f "$container_name" >/dev/null 2>&1 || true
docker create --name "$container_name" "$image_tag" noop >/dev/null

rm -rf "$out_dir/bin" "$out_dir/source"
docker cp "$container_name:/out/bin" "$out_dir/bin"
docker cp "$container_name:/out/source" "$out_dir/source"
docker rm -f "$container_name" >/dev/null

echo "build_candidate.sh: candidate $repository@$commit built at $out_dir"
echo "  bin-dir:    $out_dir/bin"
echo "  source-dir: $out_dir/source"
