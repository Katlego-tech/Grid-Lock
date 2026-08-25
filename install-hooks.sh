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
if printf 'refs/heads/main %s refs/heads/main %s\n' \
     "$(git rev-parse HEAD)" "$(git rev-parse HEAD)" \
     | bash .githooks/pre-push origin >/dev/null 2>&1; then
  echo "  FAIL -- the hook allowed a push to main. Do not rely on it; fix it first."
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
