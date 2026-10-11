#!/usr/bin/env bash
# Only the seven exact, checked branch refs below may be removed.
set -euo pipefail
: "${GH_TOKEN:?GitHub Actions token required}"
: "${GITHUB_REPOSITORY:?Repository identity required}"
: "${PRUNE_DRY_RUN:=1}"
if [[ "$GITHUB_REPOSITORY" != "Eugene999B/KOFAD" ]]; then echo "::error::Wrong repository"; exit 1; fi
if [[ "$PRUNE_DRY_RUN" != "1" && "$PRUNE_DRY_RUN" != "0" ]]; then exit 1; fi
if [[ "$PRUNE_DRY_RUN" == "0" && "${GITHUB_REF:-}" != "refs/heads/railway-release" ]]; then echo "::error::Wrong branch"; exit 1; fi
deleted=0
skipped=0
while IFS='|' read -r branch expected_sha replacement_pr archive_name; do
  [[ "$branch" == recovery/* && "$expected_sha" =~ ^[a-f0-9]{40}$ && "$archive_name" =~ ^[a-z0-9-]+$ ]] || exit 1
  current="$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/$branch" --jq '.object.sha // empty' 2>/dev/null || true)"
  if [[ -z "$current" ]]; then echo "Already absent: $branch"; continue; fi
  if [[ "$current" != "$expected_sha" ]]; then echo "::warning::Moved branch left intact: $branch"; skipped=$((skipped+1)); continue; fi
  active="$(gh api "repos/$GITHUB_REPOSITORY/pulls?state=open&head=Eugene999B:$branch&per_page=100" --jq 'length')"
  if [[ "$active" != "0" ]]; then echo "::warning::Active PR, keeping $branch"; skipped=$((skipped+1)); continue; fi
  if [[ "$replacement_pr" == "ancestor" ]]; then
    ahead="$(gh api "repos/$GITHUB_REPOSITORY/compare/railway-release...$branch" --jq '.ahead_by')"
    if [[ "$ahead" != "0" ]]; then echo "::warning::New work, keeping $branch"; skipped=$((skipped+1)); continue; fi
  else
    merged="$(gh api "repos/$GITHUB_REPOSITORY/pulls/$replacement_pr" --jq '.merged')"
    if [[ "$merged" != "true" ]]; then echo "::warning::Replacement not merged, keeping $branch"; skipped=$((skipped+1)); continue; fi
  fi
  echo "Checked obsolete branch: $branch"
  if [[ "$PRUNE_DRY_RUN" == "1" ]]; then continue; fi
  # Use the already field-tested archive function shared with branch-hygiene.yml.
  # It independently creates and GET-verifies the immutable exact-SHA tag.
  GITHUB_TOKEN="$GH_TOKEN" python - "$branch" "$current" <<'ARCHIVE_PY'
import sys
from scripts.github_branch_cleanup import archive
tag = archive(sys.argv[1], sys.argv[2])
print("Archived verified historical commit as tag:", tag)
ARCHIVE_PY
  gh api --method DELETE "repos/$GITHUB_REPOSITORY/git/refs/heads/$branch" --silent
  echo "Archived $current; removed $branch"
  deleted=$((deleted+1))
done <<'BRANCHES'
recovery/customer-email-auth-20261011|0dbe9e985a10146a5c37c3aff8572b4b5b9fa2cb|199|customer-email-auth
recovery/customer-order-created-email-20261011|e057739ab93cab30a343f4f34e21b4cdfb69f6f1|214|order-created-email
recovery/full-suite-host-hubtel-audit-20261011|c673b65037cd50c56a7936eefd2497d697b9751f|220|full-suite-hubtel-audit
recovery/market-momo-phone-verification-20261011|891542e11ffa9895f55ff39d712bd0cb09854c48|210|momo-phone-verification
recovery/order-handover-sms-after-email-20261011|5583a8b895f0d0d75ceded15a01698ad2b10d13a|ancestor|handover-sms-after-email
recovery/order-payment-handover-sms-20261011|4e4396e43a8fceb636a1d359932ba95fecb14fcc|218|handover-sms
recovery/remove-stale-worker-brand-20261011|9601d86c1b4f5f91b4cd72d0d73ec6df9f43acc6|218|obsolete-worker-brand
BRANCHES
echo "Cleanup finished. Deleted: $deleted; skipped for safety: $skipped."
