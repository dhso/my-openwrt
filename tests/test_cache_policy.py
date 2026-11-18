import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "cache_policy", Path(__file__).resolve().parents[1] / "cache-policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class CachePolicyTests(unittest.TestCase):
    def cache(self, identity, key, size=1, ref="refs/heads/main"):
        return dict(id=identity, key=key, ref=ref, size_in_bytes=size,
                    last_accessed_at=f"2026-01-{identity:02d}T00:00:00Z")

    def test_keeps_one_recent_snapshot_per_family_and_removes_legacy_keys(self):
        caches = [self.cache(1, "lede-dl-v3-Linux-old"),
                  self.cache(2, "lede-dl-v3-Linux-new"),
                  self.cache(3, "lede-build-cache-v3-Linux-old"),
                  self.cache(4, "lede-build-cache-v3-Linux-new"),
                  self.cache(5, "lede-toolchain-Linux-v2-old"),
                  self.cache(6, "lede-toolchain-Linux-v2-new"),
                  self.cache(7, "lede-dl-v2-Linux-old"),
                  self.cache(8, "lede-ccache-v2-Linux-old")]
        remove = policy.eviction_candidates(caches, "refs/heads/main", 10)
        self.assertEqual({entry["id"] for entry in remove}, {1, 3, 5, 7, 8})

    def test_never_deletes_other_branches_or_unrelated_workflow_caches(self):
        caches = [self.cache(1, "lede-dl-v3-Linux-old", ref="refs/heads/other"),
                  self.cache(2, "unrelated-cache")]
        self.assertEqual(policy.eviction_candidates(caches, "refs/heads/main", 0), [])

    def test_budget_prioritizes_expensive_toolchain_over_downloads(self):
        caches = [self.cache(1, "lede-dl-v3-Linux-new", 4),
                  self.cache(2, "lede-build-cache-v3-Linux-new", 3),
                  self.cache(3, "lede-toolchain-Linux-v2-new", 5)]
        remove = policy.eviction_candidates(caches, "refs/heads/main", 8)
        self.assertEqual([entry["id"] for entry in remove], [1])

    def test_empty_and_oversized_caches_do_not_break_budget(self):
        self.assertEqual(policy.eviction_candidates([], "refs/heads/main", 8), [])
        cache = self.cache(1, "lede-toolchain-Linux-v2-large", 9)
        self.assertEqual(policy.eviction_candidates([cache], "refs/heads/main", 8), [cache])


if __name__ == "__main__":
    unittest.main()
