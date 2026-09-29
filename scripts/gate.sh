#!/usr/bin/env bash
# gate.sh -- the one build script. Everything that decides "is this code good enough
# to leave my machine / to merge" runs here, and ONLY here.
#
#   .githooks/pre-push      -> runs this before a push
#   .github/workflows/ci.yml -> runs this on every push and PR
#
# That is the whole point of the file: the local gate and the pipeline cannot drift
# apart, because there is only one of them. A build that only works in one person's
# environment is not a build.
#
# Usage:
#   bash scripts/gate.sh                          # sweep placeholders over the whole tree
#   bash scripts/gate.sh --changed-files <path>   # sweep only the paths listed in <path>
#   bash scripts/gate.sh --list                   # print what it would run, run nothing
#   bash scripts/gate.sh --install-deps           # install every project's deps (CI uses this)
#
# Exit codes: 0 = every applicable check ran and passed. 1 = something failed, or a
# check that should have run could not run. There is deliberately no third state --
# see "the skip rule" below.
#
# ---------------------------------------------------------------------------
# THE SKIP RULE (read this before you "fix" the script by making it lenient)
#
#   A check that did not run is a FAILED check, not a passed one.
#
# The gate never reports green because it found nothing to do. If a directory has a
# pyproject.toml but no usable interpreter, that is a broken environment and the push
# is blocked. If the whole repo has no project manifest at all but the commits being
# pushed contain source files, that is a misconfigured gate and the push is blocked.
# The only silent pass is a repo that genuinely has no code in it yet.
#
# The earlier version of this gate did the opposite -- it skipped anything it could not
# run, printed "Test gate passed", and let untested code through. That is the defect
# this file exists to make impossible.
# ---------------------------------------------------------------------------

set -u

root="$(git rev-parse --show-toplevel)"
cd "$root"

changed_files_list=""
list_only=0
install_only=0

while [ $# -gt 0 ]; do
  case "$1" in
    --changed-files) changed_files_list="${2:-}"; shift 2 ;;
    --list)          list_only=1; shift ;;
    --install-deps)  install_only=1; shift ;;
    -h|--help)       sed -n '2,36p' "$0"; exit 0 ;;
    *) echo "gate.sh: unknown argument '$1'" >&2; exit 2 ;;
  esac
done

fail=0
ran=0
manifests=0

# Packages with no test suite yet, each declared against the task ID that will close
# the gap: UNTESTED["apps/web"]="T031". Empty by default -- see check_node(). This is
# the "explicitly declared stub, with a tracked follow-up" rule, applied to a whole
# package. An entry here does not make the gate pass quietly; it still reports
# the gap on every run.
declare -A UNTESTED=()

# Tunables for the checks below. They live here, not in CI or the hook, like everything else.
DUPLICATION_THRESHOLD=5   # max % of duplicated lines across the repo's source (jscpd)
JSCPD_VERSION=5.2.0       # pinned: npx fetches exactly this version, never "latest"

# Committed lockfiles of the projects found, scanned together by vuln_scan().
LOCKFILES=()

say()  { printf '%s\n' "$*"; }
step() { printf '\n-> %s\n' "$*"; }
bad()  { printf '!! %s\n' "$*"; }

# Directories that can hold a project of their own. Root first, then this repo's
# services/apps/packages layout and infra/ (compose, broker topology and schema, whose
# tests run against real Postgres and RabbitMQ), then the flat layouts people actually
# use before the microservices split happens.
project_dirs() {
  printf '%s\n' "$root"
  for d in "$root"/services/* "$root"/apps/* "$root"/packages/* "$root/infra" \
           "$root/backend" "$root/frontend" "$root/web" "$root/api"; do
    [ -d "$d" ] && printf '%s\n' "$d"
  done
}

# ---------------------------------------------------------------- Python ----
# Resolved in strict order, most-pinned first. A bare `python` on PATH is the LAST
# resort and never the assumption: on Debian/Ubuntu derivatives `python` does not
# exist at all (only `python3`), which is exactly how the old gate silently skipped
# every Python suite it was supposed to run.
py_runner=""      # how to invoke a tool, e.g. "uv run --frozen" or "/path/.venv/bin/python -m"
py_kind=""
py_venv=""        # the virtualenv the runner belongs to, when it is one
py_python=""      # that virtualenv's interpreter, when it is one

# Run a Python tool in $1, with the environment the tool expects.
#
# Picking the right interpreter is not enough for tools that *inspect* the
# environment rather than just importing from it -- pyright and pip-audit both do.
# Invoked as a bare `.venv/bin/python -m pyright`, pyright resolves imports against
# the system environment and reports phantom "could not be resolved" errors for
# packages that are plainly installed. Setting VIRTUAL_ENV is what `uv run` does for
# you, and what this reproduces.
pyrun() {
  local d="$1"; shift
  if [ -n "$py_venv" ]; then
    ( cd "$d" && VIRTUAL_ENV="$py_venv" PATH="$(path_entry "$(venv_bin "$py_venv")"):$PATH" \
        $py_runner "$@" )
  else
    ( cd "$d" && $py_runner "$@" )
  fi
}

is_python_project() {
  [ -f "$1/pyproject.toml" ] || [ -f "$1/requirements.txt" ] || [ -f "$1/setup.py" ]
}

# A uv workspace root with no [project] of its own ties its members together (one
# uv.lock, one .venv) but has no code: its members are checked one by one instead.
is_workspace_root() {
  [ -f "$1/pyproject.toml" ] \
    && grep -q '^\[tool\.uv\.workspace\]' "$1/pyproject.toml" \
    && ! grep -q '^\[project\]' "$1/pyproject.toml"
}

# The directory a virtualenv keeps its executables in: bin/ everywhere except Windows,
# where it is Scripts/. Checking only bin/ is how a Windows clone skipped its venv.
venv_bin() {
  if [ -d "$1/Scripts" ] && [ ! -d "$1/bin" ]; then printf '%s\n' "$1/Scripts"
  else printf '%s\n' "$1/bin"
  fi
}

# A directory as it must appear in $PATH. On Windows, git reports the repo as C:/Users/...,
# and Git Bash splits PATH on ':' -- so "C:/..." becomes "C" and "/...", the venv drops
# off PATH, and tools that look up `python` find the Microsoft Store stub instead.
path_entry() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"; else printf '%s\n' "$1"; fi
}

venv_python() {
  local bin; bin="$(venv_bin "$1")"
  if   [ -x "$bin/python" ];     then printf '%s\n' "$bin/python"
  elif [ -x "$bin/python.exe" ]; then printf '%s\n' "$bin/python.exe"
  else return 1
  fi
}

resolve_python() {
  local dir="$1"
  py_runner=""; py_kind=""; py_venv=""; py_python=""

  local py
  if py="$(venv_python "$dir/.venv")"; then
    py_runner="$py -m"; py_kind="venv ($dir/.venv)"; py_venv="$dir/.venv"; py_python="$py"
    return 0
  fi
  if py="$(venv_python "$root/.venv")"; then
    py_runner="$py -m"; py_kind="venv ($root/.venv)"; py_venv="$root/.venv"; py_python="$py"
    return 0
  fi
  # A workspace member is locked by the root's uv.lock, not one of its own.
  if { [ -f "$dir/uv.lock" ] || [ -f "$root/uv.lock" ]; } && command -v uv >/dev/null 2>&1; then
    py_runner="uv run --frozen"; py_kind="uv (uv.lock)"; return 0
  fi
  if command -v uv >/dev/null 2>&1 && [ -f "$dir/pyproject.toml" ]; then
    py_runner="uv run"; py_kind="uv"; return 0
  fi
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import pytest" >/dev/null 2>&1; then
      py_runner="$candidate -m"; py_kind="$candidate on PATH"; return 0
    fi
  done
  return 1
}

check_python() {
  local dir="$1" rel="$2" rc
  is_python_project "$dir" || return 0
  if is_workspace_root "$dir"; then
    say "   $rel: uv workspace root -- its members are checked one by one below"
    return 0
  fi
  manifests=$((manifests + 1))

  if ! resolve_python "$dir"; then
    bad "$rel has a Python project but no usable interpreter."
    bad "   Tried: $dir/.venv, $root/.venv, uv, python3+pytest, python+pytest."
    bad "   Fix the environment -- a gate that cannot run is not a gate that passed."
    bad "   e.g.  uv venv .venv && uv pip install -e '.[dev]'"
    fail=1
    return 0
  fi
  say "   python: $py_kind"

  if [ "$list_only" -eq 1 ]; then say "   would run: ruff check, ruff format --check, pyright, pytest ($rel)"; return 0; fi

  # Static analysis is part of the gate, not a nicety. Code rots one commit at a
  # time; the cheapest place to catch it is here.
  if pyrun "$dir" ruff --version >/dev/null 2>&1; then
    step "ruff check ($rel)"
    pyrun "$dir" ruff check . || fail=1
    step "ruff format --check ($rel)"
    pyrun "$dir" ruff format --check . || fail=1
    ran=$((ran + 1))
  else
    # The skip rule covers lint exactly as it covers tests. This used to print
    # "skipping lint" and carry on -- a skipped check reporting green.
    bad "$rel has no ruff in its environment, so its lint and format checks cannot run."
    bad "   Add it as a dev dependency:  uv add --dev ruff   (or list it in requirements.txt)"
    fail=1
  fi

  # Type checking -- the tool pyrun() sets VIRTUAL_ENV for. It catches the call that
  # passes a str where the signature says int, which lint can't see and a test only
  # sees if someone thought to write that test.
  if pyrun "$dir" pyright --version >/dev/null 2>&1; then
    step "pyright ($rel)"
    # Name the interpreter outright rather than trusting pyright to find `python` on PATH.
    if [ -n "$py_python" ]; then
      pyrun "$dir" pyright --pythonpath "$py_python" || fail=1
    else
      pyrun "$dir" pyright || fail=1
    fi
    ran=$((ran + 1))
  else
    bad "$rel has no pyright in its environment, so its type check cannot run."
    bad "   Add it as a dev dependency:  uv add --dev pyright   (or list it in requirements.txt)"
    fail=1
  fi

  step "pytest ($rel)"
  pyrun "$dir" pytest -q
  rc=$?
  ran=$((ran + 1))
  if [ "$rc" -eq 5 ]; then
    # 5 == "no tests collected". Legitimate only while a package genuinely has no
    # suite yet. It is reported loudly so it cannot quietly become permanent.
    bad "   no tests collected in $rel -- if this package has behaviour, it needs tests."
  elif [ "$rc" -ne 0 ]; then
    fail=1
  fi
}

# ------------------------------------------------------------------ Node ----
# The package manager is whatever the committed lockfile says it is, not npm by
# assumption. Running `npm run build` in a pnpm workspace either fails outright or
# silently resolves a different dependency tree than the one that was locked.
node_pm() {
  local dir="$1"
  if   [ -f "$dir/pnpm-lock.yaml" ] || [ -f "$root/pnpm-lock.yaml" ]; then echo pnpm
  elif [ -f "$dir/yarn.lock" ]      || [ -f "$root/yarn.lock" ];      then echo yarn
  elif [ -f "$dir/bun.lock" ] || [ -f "$dir/bun.lockb" ] \
       || [ -f "$root/bun.lock" ] || [ -f "$root/bun.lockb" ];        then echo bun
  else echo npm
  fi
}

# Does the package.json in $1 declare script $2? Ask package.json directly:
# `npm test --if-present` exits 0 when there is no test script at all, which reads
# as a pass.
has_script() {
  node -e 'const p=require(process.argv[1]);process.exit(p.scripts&&p.scripts[process.argv[2]]?0:1)' \
    "$1/package.json" "$2" 2>/dev/null
}

check_node() {
  local dir="$1" rel="$2" pm
  [ -f "$dir/package.json" ] || return 0
  manifests=$((manifests + 1))

  pm="$(node_pm "$dir")"
  if ! command -v "$pm" >/dev/null 2>&1; then
    bad "$rel is locked to '$pm' (its lockfile says so) but '$pm' is not on PATH."
    bad "   Install it, or the gate cannot run this package's checks."
    fail=1
    return 0
  fi

  # node_modules is checked per directory, not at the repo root. A monorepo with
  # apps/web/node_modules and nothing at the root used to skip every JS suite.
  if [ ! -d "$dir/node_modules" ] && [ ! -d "$root/node_modules" ]; then
    bad "$rel has a package.json but no installed dependencies -- its suite cannot run."
    bad "   Run '$pm install' in $rel (or at the workspace root)."
    fail=1
    return 0
  fi

  if ! command -v node >/dev/null 2>&1; then
    bad "$rel has a package.json but node is not on PATH -- its suite cannot run."
    fail=1
    return 0
  fi

  local has_lint=no has_test=no has_build=no
  has_script "$dir" lint  && has_lint=yes
  has_script "$dir" test  && has_test=yes
  has_script "$dir" build && has_build=yes

  if [ "$list_only" -eq 1 ]; then
    say "   would run ($pm): $( [ "$has_lint" = yes ] && printf 'lint ' )$( [ "$has_test" = yes ] && printf 'test ' )$( [ "$has_build" = yes ] && printf 'build ' )($rel)"
    [ "$has_lint" = no ] && say "   (no 'lint' script -- would FAIL)"
    [ "$has_test" = no ] && say "   (no 'test' script -- would FAIL)"
    return 0
  fi

  # Static analysis runs before the tests, as it does for Python. The gate can't know
  # which JS tools a package uses, so the package declares them in its `lint` script
  # -- and a package without one fails, the same way a Python project without ruff does.
  if [ "$has_lint" = yes ]; then
    step "$pm run lint ($rel)"
    ( cd "$dir" && "$pm" run lint ) || fail=1
    ran=$((ran + 1))
  else
    bad "$rel declares no 'lint' script, so its code ships unanalysed."
    bad "   Add one that runs its static checks, e.g."
    bad "   \"lint\": \"eslint . && prettier --check . && tsc --noEmit\""
    fail=1
  fi

  if [ "$has_test" = yes ]; then
    step "$pm test ($rel)"
    ( cd "$dir" && "$pm" test ) || fail=1
    ran=$((ran + 1))
  else
    # Untested code is the thing the gate exists to stop. A package that declares no
    # test script does not "have no tests to run" -- it has tests nobody wrote.
    #
    # The single exception mirrors the rule for stubs: it is allowed only if it has been
    # *explicitly declared* against a tracked follow-up. Set UNTESTED in a project-local
    # block above, e.g.
    #     UNTESTED["apps/web"]="T031"
    # That keeps the gap loud on every single run and attached to a task somebody owns,
    # which is the opposite of silencing it with a fake "test" script.
    local declared="${UNTESTED[$rel]:-}"
    if [ -n "$declared" ]; then
      bad "$rel has no test suite -- declared, tracked as $declared."
      bad "   Its behaviour is unproven. This exemption is not a pass; close $declared."
    else
      bad "$rel declares no 'test' script, so its code ships unproven."
      bad "   Write the suite. If it genuinely cannot be written yet, the package is"
      bad "   BLOCKED, not done: add a task for it and declare it via UNTESTED[\"$rel\"]."
      fail=1
    fi
  fi

  if [ "$has_build" = yes ]; then
    step "$pm run build ($rel)"
    ( cd "$dir" && "$pm" run build ) || fail=1
    ran=$((ran + 1))
  fi
}

# ---------------------------------------------------------- placeholders ----
# The central rule: nothing ships with a stub standing in for real work. This is the
# check that enforces it mechanically rather than by review.
#
# Note it sweeps the files being *pushed*, not `git diff --cached`: at pre-push time
# the index is empty, so a sweep written that way reads zero files and always passes.
placeholder_sweep() {
  step "placeholder sweep"
  local files existing=""

  if [ -n "$changed_files_list" ] && [ -f "$changed_files_list" ]; then
    files="$(cat "$changed_files_list")"
  else
    say "   no change list given -- sweeping the whole tracked tree"
    files="$(git ls-files)"
  fi

  # Excluded: markdown and docs (prose about TODOs is not a TODO), fixtures and
  # mocks (stand-in data is their job), and the gate's own files, which contain the
  # very patterns being searched for and would otherwise always fail on themselves.
  files="$(printf '%s\n' "$files" \
            | grep -Ev '^(docs/|legacy/|\.githooks/|\.github/|scripts/gate\.sh$)' \
            | grep -Ev '(^|/)(fixtures|mocks|__mocks__|testdata)/' \
            | grep -Ev '\.(md|txt|lock|svg|png|jpg|jpeg|gif|webp|ico)$' || true)"

  for f in $files; do
    [ -f "$root/$f" ] && existing="$existing $root/$f"
  done
  [ -n "$existing" ] || { say "   nothing to sweep"; return 0; }

  # `XXX` is deliberately NOT in this list. It collides with real data far too often --
  # ISO 4217's "no currency" code, redacted digits, placeholder hostnames -- and a sweep
  # that cries wolf gets disabled, which costs more than the few stubs it would catch.
  # shellcheck disable=SC2086
  if grep -nE '\b(TODO|FIXME|HACK|NotImplementedError|not implemented)\b' $existing; then
    bad "Placeholders found in the code being pushed."
    bad "   Work closed on a stub is BLOCKED, not done."
    bad "   If a stub is genuinely intended, name its tracking issue beside it"
    bad "   and it stops being a placeholder -- but say so, do not hide it."
    fail=1
  else
    say "   clean"
  fi
  ran=$((ran + 1))
}

# --------------------------------------------------------------- secrets ----
# A secret is anything that must stay private, and the cost of leaking one is not
# "fix and move on" -- once it is in git
# history it is compromised and must be rotated. So this runs before the push, which is
# the last moment it is still cheap.
#
# Pattern-matching cannot catch every secret and does not claim to. It catches the ones
# that actually leak in practice: a committed .env, a pasted key, a hardcoded password.
secret_sweep() {
  step "secret sweep"
  local files existing="" hits=0

  if [ -n "$changed_files_list" ] && [ -f "$changed_files_list" ]; then
    files="$(cat "$changed_files_list")"
  else
    files="$(git ls-files)"
  fi

  # Committed env files are the single most common leak, and no content check is
  # needed -- the filename is the finding. `.env.example` is the intended pattern.
  local envfiles
  envfiles="$(printf '%s\n' "$files" | grep -E '(^|/)\.env($|\.)' \
               | grep -Ev '\.(example|sample|template)$' || true)"
  if [ -n "$envfiles" ]; then
    bad "   env file(s) staged for push -- these belong in .gitignore, never in git:"
    printf '%s\n' "$envfiles" | sed 's/^/     /'
    hits=1
  fi

  files="$(printf '%s\n' "$files" \
            | grep -Ev '^(docs/|legacy/|\.githooks/|\.github/|scripts/gate\.sh$)' \
            | grep -Ev '\.(md|lock|svg|png|jpg|jpeg|gif|webp|ico)$' || true)"
  for f in $files; do
    [ -f "$root/$f" ] && existing="$existing $root/$f"
  done

  if [ -n "$existing" ]; then
    # Well-known key shapes, plus assignment of a secret-ish name to a literal that
    # isn't obviously a placeholder or an env lookup.
    # shellcheck disable=SC2086
    if grep -nE \
        -e '(AKIA|ASIA)[0-9A-Z]{16}' \
        -e 'sk-[A-Za-z0-9_-]{20,}' \
        -e 'ghp_[A-Za-z0-9]{36}' \
        -e 'xox[baprs]-[A-Za-z0-9-]{10,}' \
        -e '-----BEGIN [A-Z ]*PRIVATE KEY-----' \
        $existing 2>/dev/null; then
      bad "   the above matches the shape of a real credential (AWS / OpenAI / GitHub /"
      bad "   Slack token, or a private key). Rotate it -- assume it is already burned."
      hits=1
    fi
    # shellcheck disable=SC2086
    if grep -nEi '(api[_-]?key|secret|password|passwd|token|credential)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"']{12,}["'"'"']' \
        $existing 2>/dev/null \
        | grep -Eiv '(example|placeholder|changeme|your[_-]?|xxx|\*{4,}|dummy|fake|test|sample|<|\$\{|process\.env|os\.environ|getenv)'; then
      bad "   a secret-looking literal is assigned in the code above."
      bad "   Read it from the environment instead, and rotate it if it is real."
      hits=1
    fi
  fi

  if [ "$hits" -ne 0 ]; then
    bad "Secrets must never enter git history -- rewriting the commit is not enough,"
    bad "   anything already pushed must be rotated. Fix before pushing."
    fail=1
  else
    say "   clean"
  fi
  ran=$((ran + 1))
}

# ------------------------------------------------------------- lockfiles ----
# Echo the committed lockfile pinning $1's dependencies of kind $2 (py | node): the
# directory's own, else the workspace root's. Committed only -- CI's install step
# generates a lockfile when none exists, and a generated one pins nothing anyone
# reviewed. requirements.txt counts only in the project's own directory, and only
# its == pins are exact enough to scan.
committed_lockfile() {
  local dir="$1" kind="$2" d f names path
  for d in "$dir" "$root"; do
    if [ "$kind" = py ]; then
      names="uv.lock poetry.lock pdm.lock Pipfile.lock"
      [ "$d" = "$dir" ] && names="$names requirements.txt"
    else
      names="pnpm-lock.yaml yarn.lock bun.lock package-lock.json npm-shrinkwrap.json"
    fi
    for f in $names; do
      path="${d#"$root"}"; path="${path#/}"; path="${path:+$path/}$f"
      if git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
        printf '%s\n' "$path"; return 0
      fi
    done
  done
  return 1
}

# Every project needs one. Without it there is nothing exact to scan for vulnerabilities,
# and nothing reproducible to install -- so a
# missing lockfile fails here rather than letting the scan quietly cover less.
collect_lockfiles() {
  local dir="$1" rel="$2" kind lock
  is_workspace_root "$dir" && return 0   # its uv.lock is collected through its members
  for kind in py node; do
    if [ "$kind" = py ]; then is_python_project "$dir" || continue
    else [ -f "$dir/package.json" ] || continue
    fi
    if lock="$(committed_lockfile "$dir" "$kind")"; then
      case " ${LOCKFILES[*]:-} " in *" $lock "*) ;; *) LOCKFILES+=("$lock") ;; esac
    else
      bad "$rel has no committed lockfile, so its dependencies cannot be scanned for known vulnerabilities."
      if [ "$kind" = py ]; then
        bad "   Create one with  uv lock  (or pin every line of requirements.txt with ==), and commit it."
      else
        bad "   Run '$(node_pm "$dir") install' and commit the lockfile it writes."
      fi
      fail=1
    fi
  done
}

# ------------------------------------------------------- vulnerabilities ----
# Every committed lockfile, checked against OSV (which aggregates the GitHub, PyPI and
# other advisory feeds). Unlike every other check here, the result can change while the
# code doesn't: an advisory published tonight fails tomorrow's push on a branch nobody
# touched. That is the point -- a vulnerable dependency is exactly as vulnerable in
# unchanged code.
#
# Exemptions live in osv-scanner.toml at the repo root, one [[IgnoredVulns]] block each,
# under the same rule as UNTESTED: the reason names the tracked follow-up that will close
# it (a task ID like T012, or an issue like #12), and every run prints it. An ignoreUntil date makes an exemption expire on its own.
osv_exemptions() {   # prints "id<TAB>reason" per [[IgnoredVulns]] block of $1
  awk '
    function flush() { if (inblock) printf "%s\t%s\n", id, reason; inblock = 0; id = ""; reason = "" }
    /^[[:space:]]*\[\[IgnoredVulns\]\]/ { flush(); inblock = 1; next }
    /^[[:space:]]*\[/                   { flush(); next }
    inblock && /^[[:space:]]*id[[:space:]]*=/     { v = $0; sub(/^[^=]*=[[:space:]]*/, "", v); gsub(/"/, "", v); id = v }
    inblock && /^[[:space:]]*reason[[:space:]]*=/ { v = $0; sub(/^[^=]*=[[:space:]]*/, "", v); gsub(/"/, "", v); reason = v }
    END { flush() }
  ' "$1"
}

vuln_scan() {
  step "dependency vulnerability scan (osv-scanner)"
  local config="$root/osv-scanner.toml" lock id reason rc
  local args=()

  if ! command -v osv-scanner >/dev/null 2>&1; then
    bad "osv-scanner is not on PATH, so dependencies cannot be checked for known vulnerabilities."
    bad "   Install it: https://google.github.io/osv-scanner/installation/"
    bad "   e.g.  go install github.com/google/osv-scanner/v2/cmd/osv-scanner@v2.5.1"
    fail=1
    return 0
  fi

  if [ -f "$config" ]; then
    args+=(--config "$config")
    while IFS=$'\t' read -r id reason; do
      if printf '%s' "$reason" | grep -qE '(\bT[0-9]+\b|#[0-9]+\b)'; then
        bad "   accepted: $id -- $reason"
      else
        bad "   $id is ignored in osv-scanner.toml, but its reason names no tracked follow-up."
        bad "   An exemption nobody owns is a vulnerability nobody fixes: reason = \"T012: ...\" or \"#12: ...\""
        fail=1
      fi
    done < <(osv_exemptions "$config")
  fi

  for lock in "${LOCKFILES[@]}"; do args+=(-L "$root/$lock"); done
  say "   lockfiles: ${LOCKFILES[*]}"

  osv-scanner scan source "${args[@]}"
  rc=$?
  ran=$((ran + 1))
  if [ "$rc" -eq 1 ]; then
    bad "Known vulnerabilities in the dependencies above. Upgrade to the fixed version --"
    bad "   or, only if none exists yet, declare it in osv-scanner.toml against a tracked follow-up."
    fail=1
  elif [ "$rc" -ne 0 ]; then
    bad "osv-scanner could not complete (exit $rc) -- usually no network. A scan that did not"
    bad "   run is a failed scan, not a clean one."
    fail=1
  fi
}

# ------------------------------------------------------------ duplication ----
# Copy-paste across the whole repo, measured by jscpd. AI assistants produce a local copy
# of logic far more readily than they reuse the shared version, and a multi-service repo
# hides the copy well: the same helper pasted into services/a and services/b passes every
# per-service check. So this runs once over all tracked source, and fails when duplicated
# lines exceed DUPLICATION_THRESHOLD percent.
#
# Not counted: tests (repeated, explicit setup is normal there), fixtures and mocks,
# vendored or built trees, and shadcn/ui's components/ui/ -- copied in from a registry and
# alike by design. A deliberate copy is marked in the code, with the reason beside it:
#     # jscpd:ignore-start -- <why this copy is intended>
#     # jscpd:ignore-end
duplication_check() {
  step "duplication check (jscpd $JSCPD_VERSION, threshold $DUPLICATION_THRESHOLD%)"
  local files out rc

  files="$(git ls-files \
            | grep -E '\.(py|ts|tsx|js|jsx|mjs|cjs|go|rs|java|rb|php|c|cc|cpp|h|hpp|cs|kt|swift)$' \
            | grep -Ev '(^|/)(docs|legacy|fixtures|mocks|__mocks__|testdata|node_modules|vendor|dist|build|tests?|__tests__)/' \
            | grep -Ev '(^|/)components/ui/' \
            | grep -Ev '(\.(test|spec)\.[a-z]+|(^|/)test_[^/]*\.py|_test\.(py|go))$' || true)"
  if [ -z "$files" ]; then
    say "   no source files to compare"
    ran=$((ran + 1))
    return 0
  fi

  if ! command -v npx >/dev/null 2>&1; then
    bad "npx is not on PATH, so the duplication check (jscpd) cannot run. Install Node.js."
    fail=1
    return 0
  fi

  out="$(mktemp)"
  # shellcheck disable=SC2086
  ( cd "$root" && npx --yes "jscpd@$JSCPD_VERSION" --threshold "$DUPLICATION_THRESHOLD" \
      --reporters console --no-colors $files ) 2>&1 | tee "$out"
  rc=${PIPESTATUS[0]}
  ran=$((ran + 1))
  if [ "$rc" -ne 0 ]; then
    if grep -q 'too many duplicates' "$out"; then
      bad "Duplicated code is over the threshold. Reuse the shared version instead of copying it,"
      bad "   or mark a deliberate copy with jscpd:ignore-start / jscpd:ignore-end and a reason."
    else
      bad "jscpd could not run (exit $rc) -- see above. A check that did not run is a failed check."
    fi
    fail=1
  fi
  rm -f "$out"
}

# ---------------------------------------------------------------- install ---
# CI installs dependencies by calling `gate.sh --install-deps`, so the installer walks
# exactly the directories project_dirs() lists and uses exactly the package manager
# check_node() will. It used to be a loop in ci.yml with its own directory list, and
# the two drifted: CI never installed a frontend/ package that the checks then failed
# for having no node_modules. The pre-push hook never calls this -- locally, a missing
# install is yours to fix, and the checks say so rather than fixing it behind your back.
install_deps() {
  local dir="$1" rel="$2" pm

  if is_workspace_root "$dir"; then
    # One sync installs every member, with each member's dev tools, into the root .venv.
    step "install Python workspace ($rel: every member, all dependency groups)"
    if ! command -v uv >/dev/null 2>&1; then
      bad "   uv is not on PATH -- cannot install the Python workspace."
      fail=1
    else
      ( cd "$dir" && uv sync --frozen --all-packages --all-groups ) || fail=1
    fi
  elif is_python_project "$dir" && [ "$dir" != "$root" ] && is_workspace_root "$root" \
       && [ ! -f "$dir/uv.lock" ]; then
    manifests=$((manifests + 1))
    say "   $rel: workspace member -- installed by the root sync"
  elif is_python_project "$dir"; then
    manifests=$((manifests + 1))
    step "install Python dependencies ($rel)"
    if ! command -v uv >/dev/null 2>&1; then
      bad "   uv is not on PATH -- cannot install $rel's dependencies."
      fail=1
    elif [ -f "$dir/pyproject.toml" ]; then
      ( cd "$dir" && { uv sync --frozen || uv sync; } ) || fail=1
    elif [ -f "$dir/requirements.txt" ]; then
      ( cd "$dir" && { [ -d .venv ] || uv venv .venv; } && uv pip install -r requirements.txt ) || fail=1
    else
      ( cd "$dir" && { [ -d .venv ] || uv venv .venv; } && uv pip install -e . ) || fail=1
    fi
  fi

  if [ -f "$dir/package.json" ]; then
    manifests=$((manifests + 1))
    pm="$(node_pm "$dir")"
    step "$pm install ($rel)"
    if ! command -v "$pm" >/dev/null 2>&1; then
      bad "   $rel is locked to '$pm' (its lockfile says so) but '$pm' is not on PATH."
      fail=1
    elif [ "$pm" = npm ]; then
      ( cd "$dir" && { npm ci || npm install; } ) || fail=1
    else
      ( cd "$dir" && "$pm" install ) || fail=1
    fi
  fi
}

# ------------------------------------------------------------------ run -----
say "== gate: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD) =="

while IFS= read -r dir; do
  rel="${dir#"$root"/}"
  [ "$rel" = "$dir" ] && rel="."
  if [ "$install_only" -eq 1 ]; then
    install_deps "$dir" "$rel"
  else
    check_python      "$dir" "$rel"
    check_node        "$dir" "$rel"
    collect_lockfiles "$dir" "$rel"
  fi
done < <(project_dirs)

if [ "$install_only" -eq 1 ]; then
  say ""
  if [ "$fail" -ne 0 ]; then say "== dependency install FAILED =="; exit 1; fi
  say "== dependencies installed for $manifests project manifest(s) =="
  exit 0
fi

# --list stops here, before the sweeps. It used to run both of them and then report
# "nothing was executed".
if [ "$list_only" -eq 1 ]; then
  say ""
  say "-> would run: placeholder sweep, secret sweep"
  if [ "$manifests" -gt 0 ]; then
    say "-> would run: dependency vulnerability scan over: ${LOCKFILES[*]:-(no committed lockfiles)}"
    command -v osv-scanner >/dev/null 2>&1 || say "   (osv-scanner not on PATH -- would FAIL)"
    say "-> would run: duplication check (jscpd $JSCPD_VERSION, threshold $DUPLICATION_THRESHOLD%)"
    command -v npx >/dev/null 2>&1 || say "   (npx not on PATH -- would FAIL)"
  fi
  say ""
  say "(--list: no checks were run. $manifests project manifest(s) found.)"
  exit 0
fi

placeholder_sweep
secret_sweep

# Both look at the whole repo rather than one project, so they run once, and only once
# there is a project to look at.
if [ "$manifests" -gt 0 ]; then
  [ "${#LOCKFILES[@]}" -gt 0 ] && vuln_scan
  duplication_check
fi

# --- the two ways a gate lies about being green ----------------------------
if [ "$manifests" -eq 0 ]; then
  # No project manifest anywhere. Fine for a scaffold-only repo; NOT fine if the
  # commits being pushed contain source code, because then real code is going in
  # entirely unchecked and the gate would otherwise say "passed".
  code=""
  if [ -n "$changed_files_list" ] && [ -f "$changed_files_list" ]; then
    code="$(grep -E '\.(py|ts|tsx|js|jsx|go|rs|java|rb|php|c|cc|cpp|h|hpp|cs|kt|swift)$' \
              "$changed_files_list" || true)"
  fi
  if [ -n "$code" ]; then
    bad "Source files are being pushed, but this repo has no project manifest"
    bad "   (no pyproject.toml / requirements.txt / package.json anywhere the gate looks)."
    bad "   The gate cannot check this code, so it will not pretend to have checked it."
    bad "   Add the manifest, or add this directory to project_dirs() in scripts/gate.sh."
    printf '%s\n' "$code" | sed 's/^/     /'
    exit 1
  fi
  say ""
  say "No project manifests yet -- scaffold-only repo, nothing to build. Placeholder sweep ran."
elif [ "$ran" -eq 0 ]; then
  say ""
  bad "Found $manifests project manifest(s) but ran zero checks."
  bad "   That is a misconfiguration, not a pass. Fix scripts/gate.sh or the environment."
  exit 1
fi

if [ "$fail" -ne 0 ]; then
  say ""
  say "== gate FAILED =="
  exit 1
fi

say ""
say "== gate passed ($ran check(s) run across $manifests project(s)) =="
exit 0
