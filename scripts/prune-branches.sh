#!/usr/bin/env bash
# prune-branches.sh -- delete local branches whose work has landed on main.
#
#   .githooks/post-merge -> runs this after every `git pull` on main
#   bash scripts/prune-branches.sh             # prune now
#   bash scripts/prune-branches.sh --dry-run   # print what it would delete, delete nothing
#
# Merged branches are deleted on both sides, always:
#   - on the server, by the repo setting "Automatically delete head branches"
#     (gh repo edit --delete-branch-on-merge; install-hooks.sh checks it is on);
#   - in every clone, by this script. The server setting never touches a clone, so
#     without it each contributor's `git branch` fills up with finished work, and a
#     stale branch is how someone ends up committing onto work that already merged.
#
# ---------------------------------------------------------------------------
# WHAT COUNTS AS MERGED (read this before making the script more eager)
#
#   A branch is deleted only when it is PROVEN merged. Everything else is kept.
#
# Only branches that were pushed (have an upstream) are considered -- a local-only
# branch is someone's unpushed work, and this script never touches it. A pushed branch
# is merged when either:
#   1. its tip is an ancestor of origin/main (merge commit or fast-forward), or
#   2. its upstream is gone and GitHub has a pull request MERGED INTO main whose head is
#      exactly this branch's tip (squash and rebase merges rewrite the commits, so 1
#      can't see them). A branch with commits after the merged PR's head fails this
#      check and is kept -- those commits exist nowhere else. So is one whose PR merged
#      into some other branch: its work isn't on main.
# main/master and the checked-out branch are never deleted. Nor is a branch another worktree
# has checked out: git refuses to, and the worktree may hold work in progress. It is reported,
# with the worktree to remove first. The one worktree cleared without asking is one whose
# folder is gone and was inside this repo (.claude/worktrees/...): with the repo here and that
# folder not, it is not coming back. One that lived elsewhere may be on a drive that is only
# unplugged, so it is reported and left.
# ---------------------------------------------------------------------------

set -u

git() { command git -c core.quotePath=false "$@"; }

dry=0
quiet=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry=1; shift ;;
    --quiet)   quiet=1; shift ;;
    -h|--help) sed -n '2,35p' "$0"; exit 0 ;;
    *) echo "prune-branches.sh: unknown argument '$1'" >&2; exit 2 ;;
  esac
done

say() { [ "$quiet" -eq 1 ] || printf '%s\n' "$*"; }

cd "$(git rev-parse --show-toplevel)"

if ! git fetch --prune --quiet origin 2>/dev/null; then
  say "prune-branches: could not reach origin -- nothing pruned."
  exit 0
fi

base=""
for b in main master; do
  git rev-parse --verify --quiet "refs/remotes/origin/$b" >/dev/null && { base="$b"; break; }
done
[ -n "$base" ] || { say "prune-branches: no origin/main or origin/master -- nothing pruned."; exit 0; }

current="$(git symbolic-ref --short -q HEAD || true)"

# Which worktree has each branch checked out, as "branch<TAB>path" lines: `held` for a worktree
# that is there, `missing` for one whose folder is gone. Plain lists, not an associative
# array, so this still runs under the bash 3.2 that macOS ships.
top="$(pwd -P)"
held=""
missing=""
wt=""; wt_branch=""; wt_gone=0; wt_locked=0
note_worktree() {
  [ -n "$wt" ] && [ -n "$wt_branch" ] || return 0
  if [ "$wt_gone" -eq 0 ]; then
    held="$held$wt_branch	$wt
"
  elif [ "$wt_locked" -eq 0 ] && [ "${wt#"$top"/}" != "$wt" ]; then
    # Its folder was inside this repo and is gone: the entry is all that is left of it.
    [ "$dry" -eq 1 ] || git worktree remove --force "$wt" >/dev/null 2>&1 || missing="$missing$wt_branch	$wt
"
  else
    missing="$missing$wt_branch	$wt
"
  fi
}
while IFS= read -r line; do
  case "$line" in
    "worktree "*) note_worktree; wt="${line#worktree }"; wt_branch=""; wt_gone=0; wt_locked=0 ;;
    "branch "*)   wt_branch="${line#branch refs/heads/}" ;;
    prunable*)    wt_gone=1 ;;
    locked*)      wt_locked=1 ;;
  esac
done < <(git worktree list --porcelain)
note_worktree

# The path recorded for branch $2 in the list $1, if any.
worktree_of() { printf '%s' "$1" | awk -F'\t' -v b="$2" '$1 == b { print $2; exit }'; }

# The squash/rebase check needs the GitHub API. Without it, rule 1 still runs.
has_gh=0
command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1 && has_gh=1

# Heads of PRs from branch $1 that were merged into $2. Only same-repo PRs match: a PR
# opened from a fork has a different owner, so its branch is kept, never guessed at.
merged_pr_heads() {
  gh api "repos/{owner}/{repo}/pulls?state=closed&per_page=100&base=$2&head={owner}:$1" \
    --jq ".[] | select(.merged_at != null and .base.ref == \"$2\") | .head.sha" 2>/dev/null
}

deleted=0
kept=0
while IFS=$'\t' read -r branch upstream track; do
  [ -n "$upstream" ] || continue
  case "$branch" in main|master|"$current") continue ;; esac

  tip="$(git rev-parse "refs/heads/$branch")"
  reason=""
  if git merge-base --is-ancestor "$tip" "origin/$base"; then
    reason="merged into $base"
  elif [ "$track" = "[gone]" ] && [ "$has_gh" -eq 1 ] && merged_pr_heads "$branch" "$base" | grep -qx "$tip"; then
    reason="its pull request was merged into $base"
  fi

  at="$(worktree_of "$held" "$branch")"
  gone="$(worktree_of "$missing" "$branch")"
  if [ -n "$reason" ] && [ -n "$at" ]; then
    say "kept $branch: $reason, but it is checked out in the worktree at $at"
    say "   Remove the worktree, then pull again: git worktree remove \"$at\""
    kept=$((kept + 1))
  elif [ -n "$reason" ] && [ -n "$gone" ]; then
    say "kept $branch: $reason, but it belongs to a worktree whose folder is not there: $gone"
    say "   If that folder is gone for good, then pull again: git worktree prune"
    kept=$((kept + 1))
  elif [ -n "$reason" ]; then
    if [ "$dry" -eq 1 ]; then
      say "would delete $branch ($reason)"
      deleted=$((deleted + 1))
    elif git branch -D --quiet "$branch"; then
      say "deleted $branch ($reason)"
      deleted=$((deleted + 1))
    else
      say "kept $branch: $reason, but git could not delete it (see above)."
      kept=$((kept + 1))
    fi
  elif [ "$track" = "[gone]" ]; then
    say "kept $branch: deleted on the server, but no pull request merged into $base matches its tip."
    [ "$has_gh" -eq 1 ] || say "   (gh is not installed or not logged in, so squash merges can't be checked.)"
    say "   Check it, then: git branch -D $branch"
    kept=$((kept + 1))
  fi
done < <(git for-each-ref refs/heads --format='%(refname:short)%09%(upstream:short)%09%(upstream:track)')

if [ "$deleted" -eq 0 ] && [ "$kept" -eq 0 ]; then
  say "prune-branches: no merged branches to delete."
fi
exit 0
