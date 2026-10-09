#!/usr/bin/env bash
#
# Create the GitHub repository, push this history to it, and say what is left.
#
#   ./tools/bootstrap_repo.sh                      # private repo named arbiter
#   ./tools/bootstrap_repo.sh --name my-arbiter    # a different name
#   ./tools/bootstrap_repo.sh --public
#   ./tools/bootstrap_repo.sh --dry-run            # print, change nothing
#
# What this is for
# ----------------
# The sandbox this was built in cannot reach GitHub -- not this repository, not
# any repository. That is a platform limitation, not a permissions problem, and
# installing the Claude GitHub App does not change it: the App gives access to
# claude.ai/code, which is a different surface. So the push has always had to
# happen from a machine that is logged in, and "run these two commands" is the
# kind of instruction that sits in a file until somebody gets round to it.
#
# This does it in one command instead.
#
# Safety
# ------
# It never force-pushes, never rewrites history, and never touches a remote
# that already exists without saying so first. If the repository is already
# there and already has commits, it stops and tells you rather than guessing
# which side should win. Run it twice and the second run is a no-op.
#
set -uo pipefail

NAME="arbiter"
VISIBILITY="--private"
DRY_RUN=0
REMOTE="origin"

while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="${2:?--name needs a value}"; shift 2 ;;
    --public) VISIBILITY="--public"; shift ;;
    --private) VISIBILITY="--private"; shift ;;
    --remote) REMOTE="${2:?--remote needs a value}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say()  { printf '  %s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
run()  { if [ "$DRY_RUN" = 1 ]; then printf '  would run: %s\n' "$*"; else "$@"; fi; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ---------------------------------------------------------------------------
step "Checking this is the repository I think it is"

if [ ! -d .git ]; then
  say "No .git here. Unpack the archive and run this from inside the folder."
  exit 1
fi
COMMITS="$(git rev-list --count HEAD 2>/dev/null || echo 0)"
if [ "$COMMITS" = "0" ]; then
  say "No commits. Nothing to push."
  exit 1
fi
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
say "$COMMITS commits on '$BRANCH'"

if [ -n "$(git status --porcelain)" ]; then
  say "Working tree is not clean. Uncommitted changes will NOT be pushed:"
  git status --short | sed 's/^/      /'
  say "Commit them first if you want them included."
fi

# ---------------------------------------------------------------------------
step "Checking for the GitHub CLI"

HAVE_GH=0
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  HAVE_GH=1
  OWNER="$(gh api user --jq .login 2>/dev/null || echo '')"
  say "gh is installed and logged in as ${OWNER:-unknown}"
else
  if command -v gh >/dev/null 2>&1; then
    say "gh is installed but not logged in. Run: gh auth login"
  else
    say "gh is not installed (https://cli.github.com)."
  fi
  say "Falling back to printing the commands for you to run."
fi

# ---------------------------------------------------------------------------
step "Remote"

if git remote get-url "$REMOTE" >/dev/null 2>&1; then
  EXISTING="$(git remote get-url "$REMOTE")"
  say "'$REMOTE' already points at $EXISTING"
  say "Leaving it alone. Pass --remote <name> to add a second one."
  REMOTE_URL="$EXISTING"
else
  REMOTE_URL=""
fi

# ---------------------------------------------------------------------------
if [ "$HAVE_GH" = 1 ] && [ -z "$REMOTE_URL" ]; then
  step "Creating the repository"
  if gh repo view "${OWNER}/${NAME}" >/dev/null 2>&1; then
    say "${OWNER}/${NAME} already exists."
    REMOTE_COMMITS="$(gh api "repos/${OWNER}/${NAME}/commits?per_page=1" --jq 'length' 2>/dev/null || echo 0)"
    if [ "${REMOTE_COMMITS:-0}" != "0" ]; then
      say "It already has commits. I am not going to guess which history wins."
      say "Either pick another name with --name, or add the remote yourself:"
      say "    git remote add $REMOTE https://github.com/${OWNER}/${NAME}.git"
      say "    git pull --rebase $REMOTE $BRANCH   # then push"
      exit 1
    fi
    say "It is empty, so this history can go into it."
    run git remote add "$REMOTE" "https://github.com/${OWNER}/${NAME}.git"
  else
    say "Creating ${OWNER}/${NAME} (${VISIBILITY#--})"
    run gh repo create "$NAME" $VISIBILITY \
        --description "Arbiter — a repository evaluator that refuses to grade what it did not inspect" \
        --source=. --remote="$REMOTE"
  fi
  REMOTE_URL="https://github.com/${OWNER}/${NAME}.git"
fi

# ---------------------------------------------------------------------------
step "Pushing"

if [ -z "$REMOTE_URL" ]; then
  say "No remote and no gh. Create an EMPTY repository named '$NAME' on GitHub"
  say "(no README, no licence, or the first push is rejected), then run:"
  echo
  say "    git remote add $REMOTE https://github.com/<your-username>/${NAME}.git"
  say "    git push -u $REMOTE $BRANCH"
  echo
  exit 0
fi

if [ "$DRY_RUN" = 1 ]; then
  say "would run: git push -u $REMOTE $BRANCH"
else
  if git push -u "$REMOTE" "$BRANCH"; then
    say "Pushed $COMMITS commits to $REMOTE_URL"
  else
    say "Push failed. Nothing here force-pushes, so your history is intact."
    say "If the remote has commits yours does not, reconcile it yourself:"
    say "    git pull --rebase $REMOTE $BRANCH"
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
step "What happens on its own from here"

say "The nightly training job is already in .github/workflows/train.yml."
say "It runs at 08:00 UTC, caches the practice repositories, measures every"
say "rule, and commits the results back. It needs nothing from you."
if [ "$HAVE_GH" = 1 ] && [ "$DRY_RUN" != 1 ]; then
  if gh api "repos/${OWNER}/${NAME}/actions/permissions" --jq .enabled 2>/dev/null | grep -q true; then
    say "Actions are enabled on the repository."
  else
    say "Check Actions are enabled: Settings -> Actions -> General."
  fi
  say "Watch the first run with:  gh run list --repo ${OWNER}/${NAME}"
fi

step "The one step nothing can automate"

say "Installing the Claude GitHub App is a browser consent flow, so it cannot"
say "be scripted. Go to https://github.com/apps/claude and install it on this"
say "repository only."
say ""
say "That is what lets a session at claude.ai/code read and write the repo."
say "It does NOT give the Cowork sandbox access — different surface, and the"
say "sandbox cannot reach any GitHub repository at all."

step "Then, about once a fortnight"

say "Open a session at claude.ai/code with the repository connected and say:"
say ""
say "    Read training/WORKLIST.md and work the top item."
say ""
say "The nightly job keeps the evidence current; the worklist says what the"
say "evidence raises. An empty queue means the practice set has stopped"
say "teaching us anything and the next move is to widen it."
echo
