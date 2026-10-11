"""Guardrails for release branch pruning, with synthetic, no-network inputs."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "kofad_cleanup", Path(__file__).resolve().parents[1] / "scripts/github_branch_cleanup.py"
)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


class BranchCleanupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"GITHUB_REPOSITORY": "Eugene999B/KOFAD"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_main_production_open_pr_heads_and_bases_are_preserved(self):
        branches = [
            {"name": "main", "protected": False, "commit": {"sha": "main"}},
            {"name": "railway-release", "protected": False, "commit": {"sha": "live"}},
            {"name": "active/customer", "protected": False, "commit": {"sha": "a"}},
            {"name": "upstream/android", "protected": False, "commit": {"sha": "b"}},
            {"name": "merged/old", "protected": False, "commit": {"sha": "c"}},
            {"name": "protected/branch", "protected": True, "commit": {"sha": "d"}},
        ]
        prs = [
            {"state": "open", "merged_at": None, "head": {"ref": "active/customer"},
             "base": {"ref": "upstream/android"}},
            {"state": "closed", "merged_at": "2026-10-10T12:00:00Z",
             "head": {"ref": "merged/old"}, "base": {"ref": "main"}},
        ]
        def pages(path):
            return prs if "/pulls" in path else branches
        with patch.object(cleanup, "pages", side_effect=pages), patch(
            "pathlib.Path.write_text"
        ), patch.object(cleanup, "api", side_effect=AssertionError("no network in dry run")):
            report = cleanup.cleanup(dry_run=True)
        self.assertEqual([x["branch"] for x in report["planned"]], ["merged/old"])
        self.assertEqual(report["deleted"], [])
        preserved = [x["branch"] for x in report["preserved"]]
        for name in ("main", "railway-release", "active/customer", "upstream/android", "protected/branch"):
            self.assertIn(name, preserved)

    def test_routine_cleanup_never_removes_unmerged_work(self):
        branches = [
            {"name": "active/unmerged", "protected": False, "commit": {"sha": "a"}},
            {"name": "merged/old", "protected": False, "commit": {"sha": "b"}},
        ]
        prs = [{"state": "closed", "merged_at": "2026-10-10",
                "head": {"ref": "merged/old"}, "base": {"ref": "main"}}]
        with patch.object(cleanup, "pages", side_effect=lambda p: prs if "/pulls" in p else branches), patch(
            "pathlib.Path.write_text"
        ), patch.object(cleanup, "old_enough", side_effect=AssertionError("not needed")):
            report = cleanup.cleanup(dry_run=True, merged_only=True)
        self.assertEqual(len(report["planned"]), 1)
        self.assertEqual(report["planned"][0]["branch"], "merged/old")
        self.assertEqual(len(report["preserved"]), 1)

    def test_github_api_root_is_a_path_not_doubled_hostname(self):
        self.assertEqual(cleanup.ROOT, "/repos/Eugene999B/KOFAD")
        with patch.object(cleanup.urllib.request, "urlopen") as open_url:
            open_url.return_value.__enter__.return_value.read.return_value = b"{}"
            with patch.dict("os.environ", {"GITHUB_TOKEN": "fake-for-test"}):
                cleanup.api("GET", cleanup.ROOT + "/branches")
        self.assertEqual(
            open_url.call_args.args[0].full_url,
            "https://api.github.com/repos/Eugene999B/KOFAD/branches",
        )

    def test_archive_tag_must_match_exact_tip_before_deletion(self):
        with patch.object(cleanup, "api", return_value={"object": {"sha": "other"}}):
            with self.assertRaisesRegex(RuntimeError, "SHA verification failed"):
                cleanup.archive("merged/old", "desired")

    def test_production_auto_promotion_is_retired(self):
        path = Path(__file__).resolve().parents[1] / ".github/workflows/promote.yml"
        content = path.read_text()
        self.assertIn("workflow_dispatch:", content)
        self.assertNotIn("workflow_run:", content)
        self.assertNotIn("force=true", content)

if __name__ == "__main__":
    unittest.main()
