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
[ -f .githooks/post-merge ]       || { echo "!! .githooks/post-merge is missing."; exit 1; }
[ -f scripts/prune-branches.sh ]  || { echo "!! scripts/prune-branches.sh is missing."; exit 1; }

chmod +x .githooks/pre-push .githooks/post-merge scripts/gate.sh scripts/prune-branches.sh

# git reads a relative core.hooksPath from the root of whichever worktree is pushing. When the
# hooks are tracked, every worktree has them and the relative path finds them. When they are
# not in git -- the project keeps the kit's files out of it, or simply never committed them --
# a linked worktree has no .githooks at all: git finds no hook there and the push goes out
# ungated, without a word. An absolute path makes every worktree use this checkout's hooks. It
# is only used for that case, because it stops working if the folder is moved: run this script
# again after moving it (scripts/gate.sh says so when it finds the path pointing at nothing).
#
# "Not in git" means ignored, or left untracked in a repo that has commits. A repo with no
# commit yet has simply not reached its first one: the hooks are about to be tracked, and an
# absolute path there would outlive the reason for it.
hooks_path=.githooks
if git check-ignore -q .githooks/pre-push \
   || { git rev-parse -q --verify HEAD >/dev/null 2>&1 \
        && ! git ls-files --error-unmatch .githooks/pre-push >/dev/null 2>&1; }; then
  hooks_path="$root/.githooks"
  echo "  .githooks is not in git here, so worktrees get this checkout's hooks by full path."
fi
git config core.hooksPath "$hooks_path"
echo "  core.hooksPath = $(git config core.hooksPath)"

# Merged branches are deleted everywhere (docs/git-workflow.md, "Merged branches are
# deleted"). fetch.prune drops remote-tracking refs for branches deleted on the server;
# the post-merge hook then deletes the local branches that merged.
git config fetch.prune true
echo "  fetch.prune    = $(git config fetch.prune)"

# Prove it actually works, rather than assuming. A gate nobody has ever seen fire is
# indistinguishable from no gate.
echo
echo "Self-test 1/2: does the hook reject a push to main?"
#
# A non-zero exit alone proves nothing: a hook that crashes on line 1 (CRLF line endings, a bad
# shebang) also exits non-zero. So the hook must refuse for the right reason, in its own words.
# The ref line goes in on a here-string, not a pipe: a hook that exits without reading stdin
# would kill a pipe's writer, and an "allow everything" hook would then look like a crash.
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
  echo "  A hook that crashes protects nothing. If you see \$'\\r', the scripts have CRLF line"
  echo "  endings -- see .gitattributes."
  exit 1
else
  echo "  ok -- pushes to main are rejected."
fi

echo
echo "Self-test 2/2: what will the gate actually run here?"
bash scripts/gate.sh --list | sed 's/^/  /'

# The server half of branch deletion is a repo setting, which only an admin can change.
# Check it rather than assume it -- the same reason the hook is fired above.
echo
echo "Check: does the server delete a branch when its pull request merges?"
auto_delete=""
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  auto_delete="$(gh api 'repos/{owner}/{repo}' --jq .delete_branch_on_merge 2>/dev/null || true)"
fi
case "$auto_delete" in
  true)
    echo "  ok -- merged branches are deleted on the server." ;;
  false)
    echo "  NO -- merged branches pile up on the server. A repo admin turns it on with:"
    echo "    gh repo edit --delete-branch-on-merge" ;;
  *)
    echo "  unknown -- gh is missing, not logged in, or this remote isn't on GitHub. Check by"
    echo "  hand: repo Settings -> General -> \"Automatically delete head branches\"." ;;
esac

# Whether the server enforces the pull request at all depends on the plan, and a repo that
# has no protection looks exactly like one that has, until someone merges past a red check.
echo
echo "Check: does the server protect the default branch?"
protection="unknown"
default_branch=""
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  default_branch="$(gh api 'repos/{owner}/{repo}' --jq .default_branch 2>/dev/null || true)"
fi
if [ -n "$default_branch" ]; then
  # Asked of the branch itself, which anyone who can read the repo may see. The protection
  # settings endpoint answers "Not Found" to everyone but an admin, protected or not.
  protected="$(gh api "repos/{owner}/{repo}/branches/$default_branch" --jq .protected 2>/dev/null || true)"
  if [ "$protected" = true ]; then
    protection="on"
  elif [ "$protected" = false ]; then
    # Rulesets are the newer form of the same thing, and are counted separately.
    if rules="$(gh api "repos/{owner}/{repo}/rules/branches/$default_branch" --jq length 2>/dev/null)" \
       && [ "$rules" -ge 0 ] 2>/dev/null; then
      if [ "$rules" -gt 0 ]; then
        protection="on"
      else
        # Unprotected. An admin is also told when that is because the plan doesn't offer it.
        answer="$(gh api "repos/{owner}/{repo}/branches/$default_branch/protection" 2>&1 || true)"
        case "$answer" in *"Upgrade to"*) protection="unavailable" ;; *) protection="off" ;; esac
      fi
    fi
  fi
fi
case "$protection" in
  on)
    echo "  ok -- '$default_branch' has protection rules on the server. Check that they require a"
    echo "  pull request and the CI check: this only says some rule is there." ;;
  off)
    echo "  NO -- nothing on the server stops a direct push or an unreviewed merge to '$default_branch'."
    echo "  Turn it on: require a pull request and the CI check (docs/git-workflow.md, \"Three layers\")."
    echo "  If this plan doesn't offer it for a private repository, see \"When branch protection"
    echo "  isn't available\" there." ;;
  unavailable)
    echo "  not offered -- this plan has no branch protection for a private repository."
    echo "  The hook, the review and \"an AI never merges on its own\" (AGENTS.md section 4) are the"
    echo "  only barrier here. docs/git-workflow.md, \"When branch protection isn't available\", lists"
    echo "  the options; record the one you choose in PLAN.md." ;;
  *)
    echo "  unknown -- gh is missing, not logged in, this remote isn't on GitHub, or GitHub didn't"
    echo "  answer. Check by hand: repo Settings -> Branches." ;;
esac

cat <<'EOF'

Done. From here:
  - every push runs scripts/gate.sh first, and a check that cannot run counts as failed
  - pushes to main/master are rejected -- branch and open a PR
  - 'git pull' on main deletes your local branches that have merged (scripts/prune-branches.sh)
  - CI runs the same scripts/gate.sh, so local green and pipeline green mean the same thing

If the gate is ever wrong, fix scripts/gate.sh -- do not reach for --no-verify twice.
EOF
