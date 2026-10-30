#!/usr/bin/env bash
# Install git hooks that run deploy/deploy.sh in the background after
# a commit or merge. deploy.sh only acts when DEPLOY_BRANCH (main) moved.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS="$(git -C "$REPO" rev-parse --git-path hooks)"
case "$HOOKS" in /*) ;; *) HOOKS="$REPO/$HOOKS" ;; esac

for h in post-commit post-merge post-rewrite; do
  cat > "$HOOKS/$h" <<HOOK
#!/usr/bin/env bash
# installed by deploy/install-hooks.sh
nohup "$REPO/deploy/deploy.sh" >/dev/null 2>&1 &
HOOK
  chmod +x "$HOOKS/$h"
done
echo "hooks installed in $HOOKS"
