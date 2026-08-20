#!/bin/sh
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.
#
# Adds the certification candidate probe suite to a checked-out RavenProject
# source tree so it is compiled into test_raven alongside the candidate's own
# unit tests. Idempotent: re-running against an already-patched tree is a
# no-op. Takes no candidate-specific arguments; the probe calls only public,
# stable Ravencoin/Bitcoin-Core-lineage APIs and is not pinned to one release.
#
# Usage: apply-to-source.sh <source-dir>

set -eu

source_dir="$1"
probe_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

test_dir="$source_dir/src/test"
makefile="$source_dir/src/Makefile.test.include"
target="$test_dir/certification_candidate_tests.cpp"

test -d "$test_dir" || { echo "apply-to-source.sh: $test_dir not found" >&2; exit 1; }
test -f "$makefile" || { echo "apply-to-source.sh: $makefile not found" >&2; exit 1; }

cp "$probe_dir/certification_candidate_tests.cpp" "$target"

if ! grep -q 'test/certification_candidate_tests.cpp' "$makefile"; then
    # Insert next to another already-listed test source so the new entry
    # lands inside the RAVEN_TESTS list rather than after it.
    anchor='  test/kawpow_tests.cpp \'
    grep -qF "$anchor" "$makefile" || {
        echo "apply-to-source.sh: anchor line not found in $makefile" >&2
        exit 1
    }
    insertion='  test/kawpow_tests.cpp \\\n  test/certification_candidate_tests.cpp \\'
    awk -v anchor="$anchor" -v insertion="  test/certification_candidate_tests.cpp \\\\" '
        { print }
        $0 == anchor { print insertion }
    ' "$makefile" > "$makefile.tmp"
    mv "$makefile.tmp" "$makefile"
fi

grep -q 'test/certification_candidate_tests.cpp' "$makefile"
echo "apply-to-source.sh: probe applied to $source_dir"
