#!/usr/bin/env python3
"""
Terraform `external` data source program.

Replaces the old `existing` / `attachment_existing` manual YAML flags.
Terraform now finds out live, on every plan, whether a team (and its
current real members) or a team's attachment to a repo already exists on
GitHub — nothing to set, flip, or forget. If it's real, it's imported;
if it isn't (yet), it's created normally.

A 404 from GitHub here means "doesn't exist yet" — an expected, valid
result, not an error. Only a genuine auth/network failure exits non-zero
(which is how the external data source protocol surfaces a real problem
in `terraform plan` output).

Input (stdin), one of:
  {"org": "DEFRA", "kind": "team", "slug": "epr-platform"}
  {"org": "DEFRA", "kind": "attachment", "slug": "epr-platform", "repository": "epr-infrastructure-alerting"}

Output (stdout) — flat string map, per the external provider protocol
(no nesting allowed, so `members` is JSON-encoded as a string and decoded
back into a real list in imports.tf):
  team:       {"exists": "true"|"false", "id": "<numeric id, or empty>", "members_json": "<json array>"}
  attachment: {"exists": "true"|"false"}

Self-contained (stdlib only, no `requests`) — Terraform's external
provider shells out to this directly; it isn't run through any Python
virtualenv the `scripts/` tooling might use.
"""

import json
import os
import sys
import urllib.error
import urllib.request

API_ROOT = "https://api.github.com"


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(1)


def get(url: str, headers: dict):
    """Returns (status_code, parsed_json_or_None). 404 is NOT an error here.
    204 (No Content) is also a valid success — the team-repo-permissions
    check endpoint returns this by default on success, with no body, so
    json.loads() must never be attempted against it."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 204:
                return 204, None
            body = resp.read()
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return 404, None
        fail(f"GitHub API error {e.code} for {url}: {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        fail(f"Network error calling {url}: {e.reason}")


def check_team(org: str, slug: str, headers: dict) -> dict:
    status, team = get(f"{API_ROOT}/orgs/{org}/teams/{slug}", headers)
    if status == 404:
        return {"exists": "false", "id": "", "members_json": "[]"}

    members = []
    for role in ("maintainer", "member"):
        page = 1
        while True:
            _, data = get(
                f"{API_ROOT}/orgs/{org}/teams/{slug}/members?role={role}&per_page=100&page={page}",
                headers,
            )
            if not data:
                break
            members.extend({"username": m["login"], "role": role} for m in data)
            if len(data) < 100:
                break
            page += 1

    return {"exists": "true", "id": str(team["id"]), "members_json": json.dumps(members)}


def check_attachment(org: str, slug: str, repository: str, headers: dict) -> dict:
    # GET .../teams/{slug}/repos/{owner}/{repo} — 200 (with body) or 204
    # (no body, the default without a special media-type header) both mean
    # the team has access; 404 means it doesn't.
    status, _ = get(f"{API_ROOT}/orgs/{org}/teams/{slug}/repos/{org}/{repository}", headers)
    return {"exists": "true" if status in (200, 204) else "false"}


def main() -> None:
    query = json.load(sys.stdin)
    org = query.get("org")
    kind = query.get("kind")
    slug = query.get("slug")
    if not org or not kind or not slug:
        fail("Missing 'org', 'kind', or 'slug' in external data source query")

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        fail(
            "GITHUB_TOKEN (or GH_TOKEN) is not set in the environment running "
            "`terraform plan`. This script needs it to call the GitHub API."
        )

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    if kind == "team":
        print(json.dumps(check_team(org, slug, headers)))
    elif kind == "attachment":
        repository = query.get("repository")
        if not repository:
            fail("Missing 'repository' for kind=attachment")
        print(json.dumps(check_attachment(org, slug, repository, headers)))
    else:
        fail(f"Unknown kind '{kind}' — expected 'team' or 'attachment'")


if __name__ == "__main__":
    main()