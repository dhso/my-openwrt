#!/usr/bin/env python3
"""Retain this workflow's newest cache snapshots within a branch-local budget."""

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request


def cache_family(key):
    if key.startswith("lede-toolchain-"):
        return "toolchain"
    if key.startswith("lede-build-cache-v3-"):
        return "compiler"
    if key.startswith("lede-dl-v3-"):
        return "download"
    # These are owned by the same workflow, but can no longer be restored.
    if key.startswith(("lede-ccache-", "lede-dl-")):
        return "obsolete"
    return None


def eviction_candidates(caches, ref, max_bytes):
    """Never touch other refs/workflows; prefer toolchain, then compiler, then dl."""
    owned = [cache for cache in caches
             if cache["ref"] == ref and cache_family(cache["key"]) is not None]
    ordered = sorted(owned, key=lambda cache: (
        cache.get("last_accessed_at", ""), cache.get("created_at", "")), reverse=True)
    retained = set()
    remaining = max_bytes
    for family in ("toolchain", "compiler", "download"):
        latest = next((cache for cache in ordered if cache_family(cache["key"]) == family), None)
        if latest is not None and latest["size_in_bytes"] <= remaining:
            retained.add(latest["id"])
            remaining -= latest["size_in_bytes"]
    return [cache for cache in ordered if cache["id"] not in retained]


def request_json(url, token, method="GET"):
    request = urllib.request.Request(url, method=method, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        return json.loads(body) if body else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-gib", type=int, default=8)
    args = parser.parse_args()
    if args.max_gib < 0:
        parser.error("--max-gib must be nonnegative")
    repo = os.environ["GITHUB_REPOSITORY"]
    ref = os.environ["GITHUB_REF"]
    token = os.environ["GH_TOKEN"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Unexpected GitHub repository name")
    endpoint = "https://api.github.com/repos/" + repo + "/actions/caches"
    caches = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"ref": ref, "per_page": 100, "page": page})
        batch = request_json(endpoint + "?" + query, token)["actions_caches"]
        caches.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    remove = eviction_candidates(caches, ref, args.max_gib * 1024 ** 3)
    for cache in remove:
        try:
            request_json(endpoint + "/" + str(int(cache["id"])), token, method="DELETE")
            print("Removed obsolete build cache: " + cache["key"])
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
    print(f"Cache policy: removed {len(remove)} snapshots; budget {args.max_gib} GiB for {ref}")


if __name__ == "__main__":
    main()
