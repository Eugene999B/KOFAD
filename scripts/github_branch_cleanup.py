"""Archive old GitHub branches as exact-SHA tags before deletion.

Never delete main, production, protected branches, open PR heads/bases, or
recent unpublished work. Every deletion uses git's atomic force-with-lease.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "Eugene999B/KOFAD"
ROOT = "/repos/" + REPO
PREFIX = "kofad-branch-archive-20261011"
KEEP = {"main", "railway-release"}


def api(method, path, body=None):
    token = os.environ["GITHUB_TOKEN"]
    req = urllib.request.Request(
        "https://api.github.com" + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "KOFAD-branch-cleanup",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.loads(response.read() or b"{}")


def pages(path):
    data = []
    for page in range(1, 10):
        separator = "&" if "?" in path else "?"
        batch = api("GET", ROOT + path + separator + "per_page=100&page=" + str(page))
        if not isinstance(batch, list):
            raise RuntimeError("Branch/PR inventory is incomplete")
        data.extend(batch)
        if len(batch) < 100:
            return data
    raise RuntimeError("More than 800 results: refuse to prune")


def branch_info(name):
    return api("GET", ROOT + "/branches/" + urllib.parse.quote(name, safe="/"))


def archive(name, sha):
    tag = PREFIX + "/" + name
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", tag):
        raise RuntimeError("Unsupported tag name")
    path = ROOT + "/git/ref/tags/" + urllib.parse.quote(tag, safe="/")
    try:
        value = api("GET", path)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        api("POST", ROOT + "/git/refs", {"ref": "refs/tags/" + tag, "sha": sha})
        value = api("GET", path)
    if value.get("object", {}).get("sha") != sha:
        raise RuntimeError("Archive tag SHA verification failed")
    return tag


def old_enough(name):
    sha = branch_info(name)["commit"]["sha"]
    info = api("GET", ROOT + "/commits/" + sha)
    iso = info["commit"]["committer"]["date"]
    date = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (dt.datetime.now(dt.timezone.utc) - date).total_seconds() >= 12 * 3600


def cleanup(dry_run=True, merged_only=False):
    if os.environ.get("GITHUB_REPOSITORY") != REPO:
        raise RuntimeError("Refusing to operate on a different repository")
    branches, prs = pages("/branches"), pages("/pulls?state=all")
    if not 2 <= len(branches) <= 400:
        raise RuntimeError("Unexpected branch count: abort cleanup")
    open_refs, merged_refs = set(), set()
    for pr in prs:
        if pr["state"] == "open":
            open_refs.update((pr["head"]["ref"], pr["base"]["ref"]))
        if pr.get("merged_at"):
            merged_refs.add(pr["head"]["ref"])
    report = {
        "branch_count_before": len(branches),
        "mode": "dry-run" if dry_run else "archive-then-delete",
        "deleted": [], "planned": [], "preserved": [], "failed": [],
    }
    for row in branches:
        name, sha = row["name"], row["commit"]["sha"]
        if name in KEEP or name in open_refs or row.get("protected"):
            report["preserved"].append({"branch": name, "reason": "protected/default/open PR"})
            continue
        if merged_only and name not in merged_refs:
            report["preserved"].append({"branch": name, "reason": "unmerged work"})
            continue
        try:
            if name not in merged_refs and not old_enough(name):
                report["preserved"].append({"branch": name, "reason": "recent unpublished work"})
                continue
            record = {"branch": name, "sha": sha, "tag": PREFIX + "/" + name}
            if dry_run:
                report["planned"].append(record)
                continue
            if branch_info(name)["commit"]["sha"] != sha:
                report["preserved"].append({"branch": name, "reason": "tip advanced"})
                continue
            # Re-query open PRs to avoid races with another working session.
            current_prs = pages("/pulls?state=open")
            if any(name in (p["head"]["ref"], p["base"]["ref"]) for p in current_prs):
                report["preserved"].append({"branch": name, "reason": "new open PR"})
                continue
            archive(name, sha)
            if branch_info(name)["commit"]["sha"] != sha:
                report["preserved"].append({"branch": name, "reason": "tip advanced after archive"})
                continue
            # Atomic deletion only if the branch's remote SHA still equals sha.
            subprocess.run([
                "git", "push", "origin", "--porcelain",
                "--force-with-lease=refs/heads/" + name + ":" + sha,
                ":refs/heads/" + name,
            ], check=True, capture_output=True, text=True, timeout=45)
            report["deleted"].append(record)
        except Exception as exc:
            report["failed"].append({"branch": name, "error": str(exc)[:200]})
        if len(report["deleted"]) and len(report["deleted"]) % 25 == 0:
            print("Deleted", len(report["deleted"]), "archive-tagged branches", flush=True)
    Path("branch-cleanup-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: len(value) if isinstance(value, list) else value
                     for key, value in report.items()}, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--merged-only", action="store_true")
    args = parser.parse_args()
    report = cleanup(not args.apply, args.merged_only)
    if report["failed"]:
        raise SystemExit("Some branches could not be archived or deleted; see artifact")
