#!/usr/bin/env bash
# install-hooks.sh -- run once per clone, by every contributor.
#
# `core.hooksPath` is local config: it lives in .git/config, which is NOT cloned.
# So the person who bootstrapped the repo has the gate and nobody else does, silently,
# until they run this. That is why it is a script with a self-test rather than a line
# of prose in a README that everyone skims past.

set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"

echo "Installing the pre-push gate for this clone..."

[ -f .githooks/pre-push ] || { echo "!! .githooks/pre-push is missing."; exit 1; }
[ -f scripts/gate.sh ]    || { echo "!! scripts/gate.sh is missing."; exit 1; }

chmod +x .githooks/pre-push scripts/gate.sh
git config core.hooksPath .githooks
echo "  core.hooksPath = $(git config core.hooksPath)"

# Prove it actually works, rather than assuming. A gate nobody has ever seen fire is
# indistinguishable from no gate.
echo
echo "Self-test 1/2: does the hook reject a push to main?"
#
# A non-zero exit alone proves nothing: a hook that crashes on line 1 (CRLF line
# endings, a bad shebang) also exits non-zero, and would pass a test that only looks
# at the exit code. So the hook must refuse for the right reason -- its own message.
#
# The ref line goes in on a here-string, not a pipe: a hook that exits without reading
# stdin would otherwise kill the writer with SIGPIPE (exit 141), and an "allow
# everything" hook would be misreported as a crash instead of as the open door it is.
head_sha="$(git rev-parse HEAD)"
hook_rc=0
hook_out="$(bash .githooks/pre-push origin 2>&1 \
     <<< "refs/heads/main $head_sha refs/heads/main $head_sha")" || hook_rc=$?
if [ "$hook_rc" -eq 0 ]; then
  echo "  FAIL -- the hook allowed a push to main. Do not rely on it; fix it first."
  exit 1
elif ! printf '%s\n' "$hook_out" | grep -q "Direct pushes to 'main' are not allowed"; then
  echo "  FAIL -- the hook exited $hook_rc, but not because it rejected main. It crashed:"
  printf '%s\n' "$hook_out" | head -n 5 | sed 's/^/    /'
  echo "  A hook that crashes protects nothing. If you see \$'\\r', the scripts have"
  echo "  CRLF line endings -- see .gitattributes."
  exit 1
else
  echo "  ok -- pushes to main are rejected."
fi

echo
echo "Self-test 2/2: what will the gate actually run here?"
bash scripts/gate.sh --list | sed 's/^/  /'

cat <<'EOF'

Done. From here:
  - every push runs scripts/gate.sh first, and a check that cannot run counts as failed
  - pushes to main/master are rejected -- branch and open a PR
  - CI runs the same scripts/gate.sh, so local green and pipeline green mean the same thing

If the gate is ever wrong, fix scripts/gate.sh -- do not reach for --no-verify twice.
EOF
