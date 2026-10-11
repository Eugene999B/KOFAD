# KOFAD protected release and GitHub branch hygiene

**Source of truth for the live website and communications worker: `railway-release`.**
The `main` branch and any open pull-request head/base must never be deleted automatically. `main` is not a production deployment target.

## Released-code promotion
- Automatic `main` → `railway-release` promotion has been disabled in `.github/workflows/promote.yml`. No success on an outdated default branch should rewrite the live code.
- Target new production PRs at `railway-release`, starting from the current live tip. Inspect the exact diff, require passing **Verify KOFAD** tests, then squash/merge without force. Check Railway web and communications worker separately after the merge.
- Keep native Android, app signing and Appetize experiments in reviewed feature PRs. Do not merge entire historical branches to recover one file.
- The comparison between old main and production originally showed **divergent histories** with unique Android QA and payroll commits. Never perform a blind force reset or automatic reconcile. A history-preserving, non-force merge must use a deliberately reviewed tree.
- Turn on the GitHub ruleset for `railway-release` and `main`: prevent force pushes and deletion, require PR review, require the verification checks, restrict who can bypass the rules, and consider CODEOWNERS for `.github/workflows/`, payments and migrations. These repository administration settings require an owner with GitHub Settings access.

## Cleanup safety
- On the initial explicit cleanup, `.github/workflows/branch-hygiene.yml` creates a verified **Git tag holding the original branch tip** before it can delete any obsolete branch. Open PR heads **and bases**, `main`, `railway-release`, and GitHub-protected branches are never deleted.
- The first run may prune old unpublished/abandoned branches only when they have not advanced in at least 12 hours. If a developer has an open PR for a branch, that branch is never eligible.
- Future automatic release cleanup only prunes branches already associated with a **merged PR**. It never removes unmerged experiments on its own.
- Every deletion is an atomic `--force-with-lease` operation; changed branch tips cannot be deleted. Any failed archive or deletion leaves the branch in place, and the job reports it.
- GitHub Actions saves a branch-to-SHA archive manifest as a downloadable workflow artifact. Tags remain in the repository independently of the artifact lifetime.

To recover an archived branch: find its exact tip at `refs/tags/kofad-branch-archive-20261011/<original-name>`, verify the SHA against the cleanup audit artifact, then make a new branch from that tag. **Never** point `main` or `railway-release` at that tag.

## Manual fallback
Open GitHub → repository **Settings → General → Pull Requests** and enable **Automatically delete head branches**. This applies to future merged PRs; do not let it replace the separate release rulesets.

When approving another production release, check the integration test suite, customer and staff authentication, payment evidence, messaging worker, official KOFAD branding, Android API compatibility, and the latest encrypted Google Drive backup. A green deployment is not the same as a completed live payment or an isolated database restore.
