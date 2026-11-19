#!/bin/bash
# Run a command in DIR with no network, no home folder and no secrets in the environment.
# Only the system, uv and uv's Pythons are visible, read-only; DIR is the one writable place.
#
#   sandboxed.sh DIR uv run pytest -q
#
# Install the dependencies first, outside, with `uv sync --frozen` in DIR: inside there is no network.
set -euo pipefail
DIR=$(realpath "$1"); shift
UV=$(command -v uv) || { echo "uv is not installed" >&2; exit 1; }
PYTHONS=$(uv python dir)
command -v bwrap >/dev/null || { echo "bubblewrap (bwrap) is not installed" >&2; exit 1; }
exec bwrap --clearenv --setenv LANG C.UTF-8 --unshare-all --die-with-parent --new-session \
  --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/bin /bin --symlink usr/bin /sbin \
  --ro-bind-try /etc/alternatives /etc/alternatives --ro-bind-try /etc/ssl /etc/ssl \
  --ro-bind-try /etc/ca-certificates /etc/ca-certificates --ro-bind-try /etc/localtime /etc/localtime \
  --proc /proc --dev /dev --tmpfs /tmp --tmpfs /home \
  --ro-bind "$PYTHONS" "$PYTHONS" --dir /opt/uvbin --ro-bind "$UV" /opt/uvbin/uv \
  --bind "$DIR" "$DIR" --chdir "$DIR" \
  --setenv HOME /tmp/home --setenv PATH /opt/uvbin:/usr/bin --setenv UV_NO_SYNC 1 --setenv UV_OFFLINE 1 \
  --setenv UV_CACHE_DIR /tmp/uvcache --setenv UV_PYTHON_DOWNLOADS never \
  "$@"
