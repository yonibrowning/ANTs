#!/usr/bin/env bash
# Code Ocean environment "postInstall" script: build this ANTs fork into /opt/ants and make it
# the ANTs that commandants (and anything else using PATH) runs.
#
# It runs once when the capsule environment is built, so the compiled tools are part of the
# image rather than rebuilt on every run. Paste it into the capsule's postInstall (Environment
# > Post-install script), or call it from there.
#
#   ANTS_REPO  git URL to build      (default: the yonibrowning fork)
#   ANTS_REF   branch, tag or commit (default: fast-syn)
#   ANTS_JOBS  parallel build jobs   (default: all cores)
set -euo pipefail

ANTS_REPO="${ANTS_REPO:-https://github.com/yonibrowning/ANTs.git}"
ANTS_REF="${ANTS_REF:-fast-syn}"
ANTS_JOBS="${ANTS_JOBS:-$(nproc)}"
PREFIX=/opt/ants
WORK=/tmp/ants-build

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates git g++ make ninja-build zlib1g-dev
# ANTs and ITK need CMake >= 3.22.1; older distro packages are too old, so fall back to pip.
need_cmake() {
  ! command -v cmake >/dev/null 2>&1 && return 0
  [ "$(printf '%s\n3.22.1\n' "$(cmake --version | head -n1 | awk '{print $3}')" | sort -V | head -n1)" != "3.22.1" ]
}
if need_cmake; then
  apt-get install -y --no-install-recommends cmake || true
fi
if need_cmake; then
  python3 -m pip install --no-cache-dir "cmake>=3.24"
fi

git config --global url.'https://'.insteadOf 'git://'
rm -rf "$WORK" && mkdir -p "$WORK"
git clone --branch "$ANTS_REF" "$ANTS_REPO" "$WORK/src" || {
  git clone "$ANTS_REPO" "$WORK/src" && git -C "$WORK/src" checkout "$ANTS_REF"; }

cmake -S "$WORK/src" -B "$WORK/build" -GNinja \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF \
  -DBUILD_TESTING=OFF -DRUN_SHORT_TESTS=OFF -DRUN_LONG_TESTS=OFF \
  -DCMAKE_INSTALL_PREFIX="$PREFIX"
cmake --build "$WORK/build" --parallel "$ANTS_JOBS"
cmake --install "$WORK/build/ANTS-build"

# Record exactly what was built.
git -C "$WORK/src" rev-parse HEAD > "$PREFIX/ANTS_COMMIT"
rm -rf "$WORK"

# Make this build the one everything finds, commandants included. commandants searches
# ants_path= -> $ANTSPATH -> PATH -> its own downloaded copy. Capsule runs start from a
# non-interactive shell that never reads profile files, so an exported ANTSPATH would not
# reach them; links in /usr/local/bin (on PATH for every process) do. ANTSPATH is still set
# for terminal sessions.
for f in "$PREFIX"/bin/*; do
  ln -sf "$f" /usr/local/bin/
done
echo "export ANTSPATH=$PREFIX/bin" > /etc/profile.d/ants.sh

# Fail the environment build if anything would pick up a different ANTs.
found="$(command -v antsRegistration || true)"
if [ "$(readlink -f "$found")" != "$PREFIX/bin/antsRegistration" ]; then
  echo "ERROR: antsRegistration on PATH is '$found', not $PREFIX/bin. Another ANTs comes" >&2
  echo "earlier on PATH ($PATH); remove it or set ANTSPATH=$PREFIX/bin for the capsule." >&2
  exit 1
fi
# Run from / so a folder that happens to be called "commandants" isn't imported instead.
cd /
if command -v python3 >/dev/null 2>&1 && python3 -c "import commandants.core.executable" 2>/dev/null; then
  python3 - "$PREFIX/bin" <<'EOF'
import os, sys
from commandants.core.executable import resolve_binary
want = os.path.realpath(os.path.join(sys.argv[1], "antsRegistration"))
got = os.path.realpath(resolve_binary("antsRegistration"))
if got != want:
    sys.exit("ERROR: commandants resolves antsRegistration to %s, not %s" % (got, want))
print("commandants uses", got)
EOF
else
  echo "commandants is not installed in this environment yet; once it is, it will find" \
       "this build on PATH (/usr/local/bin -> $PREFIX/bin)."
fi

echo "ANTs installed in $PREFIX/bin (commit $(cat $PREFIX/ANTS_COMMIT)), linked into /usr/local/bin."
