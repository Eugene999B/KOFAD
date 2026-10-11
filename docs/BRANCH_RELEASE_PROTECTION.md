# KOFAD GitHub release and branch protection policy

## Canonical branches

- `railway-release` is **production**. Railway deploys only approved commits from this branch. Never force-push, reset, merge old snapshots directly, or restore an old code tree over this branch.
- `main` is the **default integration branch**, not a separate legacy copy to deploy to Railway. Its Django application source was checked against `railway-release` on 2026-10-11; current tree differences were isolated to Android GitHub Actions workflows, which must be reviewed rather than discarded.
- Short-lived `feature/*` and `recovery/*` branches are never deployable. A pull request into `railway-release` must start from the current release, preserve concurrent features, and pass all required tests before merging.

## Cleanup performed 2026-10-11

Seven obsolete recovery branches were SHA-verified, archived under immutable-looking `kofad-branch-archive-20261011/recovery/*` tags, and actually deleted. The exact deletion and archive log is in the successful GitHub Actions run for commit `a14ebc690fd7f61c1bebed28cd80e1f81fa67bff` (workflow `Archive and prune verified obsolete KOFAD branches`). Existing `branch-hygiene.yml` automatically archives and deletes eligible merged branches after subsequent release promotions.

Retained: `main`, `railway-release`, one recovery email-centre-trash branch with independent unpublished changes, and feature branches with open or otherwise unmerged work (Android/Firebase, backup, export studio, email trash). **Do not delete unmerged feature branches until the integrated replacement PR is merged and verified**, especially Android integration #188.

The temporary seven-branch deletion workflow and shell script were retired after success. No broad deleting wildcard remains from that one-shot cleanup.

## Production protection settings — **GitHub owner action required**

As checked 2026-10-11, the repository's Rulesets API returned **no configured rulesets**. Connected GitHub integration lacks the administration API scope to create or enforce these settings. Owner must configure them directly:

1. In **GitHub → Eugene999B/KOFAD → Settings → Rules → Rulesets → New branch ruleset**, target `railway-release`.
2. **Block branch deletion and force pushes.** Require pull requests and at least one independent approving review. Require all threads resolved. Do not allow bypass by administrators or automations except a tightly controlled emergency break-glass process.
3. Require the **Verify KOFAD** GitHub Actions status check to pass before merging. Require branches to be up to date with `railway-release` before merging (or at minimum rerun tests with the latest base). Consider requiring the focused release-regression guard PR status too; the existing post-push job is primarily monitoring and does not alone block merges.
4. Create a corresponding ruleset for `main`: no force pushes/deletions; PR review and successful `Verify KOFAD` CI.
5. Enable **Automatically delete head branches** in repository Settings → General → Pull Requests, so successfully merged feature branches are removed after archival and no longer accumulate.
6. Restrict Railway production auto-deploy to `railway-release` and keep GitHub deployment credentials, notification credentials and production database secrets out of branch source code.
7. Verify rulesets are **Active** and confirm an intentionally failing test PR cannot merge. Re-check after settings changes; a written policy or passing CI alone does not enforce a branch rule.

## Before any production promotion

- Check the PR base and current production SHA; do not merge a months-old feature branch without updating its base.
- Read the diff for accidental removed routes, views, UI, payments, notifications, backup and schema migrations.
- Run `Verify KOFAD` plus the narrow affected regression suites against disposable test data.
- Ensure new Android/Firebase components and workers from other development branches were preserved.
- Promote only the PR that passed tests; confirm Railway `kofad-web`, `kofad-sms`, and PostgreSQL health afterwards.
- Never try a backup restore, database reset, live charge or real-message test just to validate code promotion.

Track rollback recovery in [incident #195](https://github.com/Eugene999B/KOFAD/issues/195).
