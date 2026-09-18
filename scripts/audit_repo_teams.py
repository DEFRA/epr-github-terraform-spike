#!/usr/bin/env python3
"""
Step 1: for a set of repos, produce a full team/access audit —
which teams have access to each repo (and at what permission), and each
team's full member list with role (maintainer/member).

This replaces and merges get_repo_teams.py (repo -> team listing, Excel
export) and audit_repos.py (repo -> team -> live members, JSON output).
Both did overlapping work slightly differently; this is the one script for
"what does GitHub actually look like right now", usable both as a one-off
discovery report and as the source of truth for writing repositories.yaml
(members lists here should be pasted in verbatim when setting
`existing: true` on a team).

Repo list can come from three places (pick one):
  --org + --term      auto-discover via name/topic match (delegates to
                       discover_repos.py's get_match_repos)
  --repos-file PATH    a plain text file, one repo name per line (# comments
                       and blank lines ignored)
  --from-yaml PATH     an existing repositories.yaml — audits exactly the
                       repos already listed in it (this is what audit_repos.py
                       did, kept here as a mode rather than a separate script)

Output: JSON (default, matches the shape of the old audit_repos.py output)
and/or a flat CSV/XLSX table (one row per team-per-repo, matches the old
get_repo_teams.py output) via --table-out.

Usage:
  python3 audit_repo_teams.py --org DEFRA --term epr --table-out epr-audit.xlsx
  python3 audit_repo_teams.py --repos-file epr-repos.txt --json-out audit.json
  python3 audit_repo_teams.py --from-yaml config/repositories.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import yaml

from discover_repos import get_match_repos
from github_api_common import (
    API_ROOT,
    api_get,
    build_headers,
    get_org,
    get_team_members_with_roles,
    get_token,
    write_table,
)


def load_repo_names_from_file(path: str) -> list[str]:
    if not os.path.exists(path):
        print(f"Error: '{path}' not found.", file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def load_repo_names_from_yaml(path: str) -> list[str]:
    with open(path) as f:
        config = yaml.safe_load(f) or {}
    return [r["name"] for r in config.get("repos", [])]


def get_repo_teams(org: str, repo_name: str, headers: dict) -> list[dict]:
    """Teams with access to a repo, including parent-team info — the
    parent_team_id is exactly what repositories.yaml needs for a child team."""
    teams = api_get(f"{API_ROOT}/repos/{org}/{repo_name}/teams", headers, params={"per_page": 100})
    if not teams:
        return []
    result = []
    for t in teams:
        parent = t.get("parent")
        result.append(
            {
                "name": t.get("name"),
                "slug": t.get("slug"),
                "permission": t.get("permission"),
                "parent_team_name": parent.get("name") if parent else None,
                "parent_team_id": parent.get("id") if parent else None,
            }
        )
    return result


def get_direct_collaborators(org: str, repo_name: str, headers: dict) -> list[dict]:
    collabs = api_get(
        f"{API_ROOT}/repos/{org}/{repo_name}/collaborators", headers, params={"affiliation": "direct", "per_page": 100}
    )
    if not collabs:
        return []
    return [{"login": c.get("login"), "permission": c.get("permissions")} for c in collabs]


def audit_repos(org: str, repo_names: list[str], headers: dict) -> dict:
    repo_audit: dict = {}
    table_rows: list[dict] = []

    for i, repo_name in enumerate(repo_names, start=1):
        print(f"Auditing ({i}/{len(repo_names)}): {repo_name}...", file=sys.stderr)

        teams = get_repo_teams(org, repo_name, headers)
        direct_collabs = get_direct_collaborators(org, repo_name, headers)

        teams_list = []
        if not teams:
            table_rows.append(
                {
                    "Repo": repo_name,
                    "TeamName": "-",
                    "TeamSlug": "-",
                    "Permission": "-",
                    "ParentTeam": "-",
                    "ParentTeamID": "-",
                    "Members": "-",
                }
            )
        for t in teams:
            members = get_team_members_with_roles(org, t["slug"], headers)
            teams_list.append(
                {
                    "name": t["name"],
                    "slug": t["slug"],
                    "permission": t["permission"],
                    "parent_team_id": t["parent_team_id"],
                    "members": members,
                }
            )
            table_rows.append(
                {
                    "Repo": repo_name,
                    "TeamName": t["name"],
                    "TeamSlug": t["slug"],
                    "Permission": t["permission"],
                    "ParentTeam": t["parent_team_name"] or "-",
                    "ParentTeamID": t["parent_team_id"] or "-",
                    "Members": "; ".join(f"{m['username']}:{m['role']}" for m in members) or "-",
                }
            )

        repo_audit[repo_name] = {"teams": teams_list, "direct_collaborators": direct_collabs}

    return {"repo_audit": repo_audit, "table_rows": table_rows}


def main():
    parser = argparse.ArgumentParser(description="Audit team access and membership for a set of repos.")
    parser.add_argument("--org", help="GitHub organisation (or set GITHUB_ORG)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--term", help="Auto-discover repos matching this name/topic term")
    source.add_argument("--repos-file", help="Text file of repo names, one per line")
    source.add_argument("--from-yaml", help="Audit exactly the repos listed in this repositories.yaml")
    parser.add_argument("--json-out", help="Write full nested JSON audit to this path (default: stdout)")
    parser.add_argument("--table-out", help="Write a flat one-row-per-team table (.csv or .xlsx)")
    args = parser.parse_args()

    token = get_token()
    org = get_org(args.org)
    headers = build_headers(token)

    if args.term:
        repo_names = sorted(r["name"].split("/")[-1] for r in get_match_repos(org, args.term, headers))
    elif args.repos_file:
        repo_names = load_repo_names_from_file(args.repos_file)
    else:
        repo_names = load_repo_names_from_yaml(args.from_yaml)

    if not repo_names:
        print("No repos to audit.", file=sys.stderr)
        sys.exit(0)

    result = audit_repos(org, repo_names, headers)

    output_json = json.dumps({"repo_audit": result["repo_audit"]}, indent=2)
    if args.json_out:
        with open(args.json_out, "w") as f:
            f.write(output_json)
        print(f"Wrote JSON audit to {os.path.abspath(args.json_out)}")
    else:
        print(output_json)

    if args.table_out:
        write_table(result["table_rows"], args.table_out)


if __name__ == "__main__":
    main()
