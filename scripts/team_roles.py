#!/usr/bin/env python3
"""
Terraform `external` data source program.

Terraform's external data source protocol: this script receives a JSON
object on stdin (the `query` map from the data source block) and must print
exactly one JSON object of string -> string to stdout. No nesting is
allowed in the output, so a list value (the members) is JSON-encoded as a
single string and decoded back into a real list in locals.tf.

Input (stdin):  {"org": "ABCD", "slug": "epr-admins"}
Output (stdout): {"members_json": "[{\"login\": \"alice\", \"role\": \"maintainer\"}, ...]"}

Why this exists: neither the `github_team` nor `github_repository_teams`
Terraform data source exposes each member's team role (member vs
maintainer) — only a flat list of logins. The REST API's role filter
(?role=maintainer / ?role=member) gives us that, so this script calls it
directly, once per (repo, team) pair.
"""

import json
import os
import sys
import urllib.error
import urllib.request

API_ROOT = "https://api.github.com"


def fail(message: str) -> None:
    # The external data source protocol requires a clean JSON object on
    # success. On failure, printing to stderr and exiting non-zero is how
    # Terraform surfaces the error in `terraform plan` output.
    print(message, file=sys.stderr)
    sys.exit(1)


def api_get(url: str, headers: dict) -> list:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        fail(f"GitHub API error {e.code} for {url}: {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        fail(f"Network error calling {url}: {e.reason}")


def main() -> None:
    query = json.load(sys.stdin)
    org = query.get("org")
    slug = query.get("slug")
    if not org or not slug:
        fail("Missing 'org' or 'slug' in external data source query")

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

    members = []
    for role in ("maintainer", "member"):
        url = f"{API_ROOT}/orgs/{org}/teams/{slug}/members?role={role}&per_page=100"
        for m in api_get(url, headers):
            members.append({"login": m["login"], "role": role})

    print(json.dumps({"members_json": json.dumps(members)}))


if __name__ == "__main__":
    main()
