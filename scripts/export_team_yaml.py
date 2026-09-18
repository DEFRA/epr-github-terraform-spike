#!/usr/bin/env python3
"""
Helper for populating repositories.yaml `teams:` entries from real GitHub
state. Two things this replaces:

  get_org_teams.py    -> --list          (find a team's exact slug first)
  get_team_members.py -> --yaml SLUG     (get one team's ready-to-paste block)

Usage:
  python3 export_team_yaml.py --list                  # every team in the org
  python3 export_team_yaml.py --yaml epr-platform      # one team's YAML block
  python3 export_team_yaml.py --yaml epr-platform --set-permission admin
"""

from __future__ import annotations

import argparse
import sys

import yaml

from github_api_common import API_ROOT, api_get, build_headers, get_org, get_team_members_with_roles, get_token, paginated_get


def list_org_teams(org: str, headers: dict) -> list[dict]:
    teams = paginated_get(f"{API_ROOT}/orgs/{org}/teams", headers)
    return [{"slug": t["slug"], "name": t["name"], "id": t["id"]} for t in teams]


def build_team_yaml_block(org: str, team_slug: str, headers: dict, permission: str) -> str:
    team = api_get(f"{API_ROOT}/orgs/{org}/teams/{team_slug}", headers)
    if team is None:
        print(f"Error: team '{team_slug}' not found in org '{org}'.", file=sys.stderr)
        sys.exit(1)

    parent = team.get("parent")
    members = get_team_members_with_roles(org, team_slug, headers)

    block: dict = {"slug": team_slug, "name": team.get("name", team_slug)}
    if parent:
        block["parent_team_id"] = parent["id"]
    block["permission"] = permission
    # This team is real on GitHub right now — the whole point of this
    # script — so it's exported ready for the `existing: true` import
    # path, not the create path. attachment_existing is deliberately left
    # False: this script only knows the team exists, not whether it's
    # already attached to whichever repo you're about to paste this into.
    block["existing"] = True
    block["attachment_existing"] = False  # set True yourself if this team is already attached to the target repo
    block["members"] = members

    yaml_str = yaml.dump([block], sort_keys=False, default_flow_style=False)
    # Indent to fit under a repo's `teams:` list in repositories.yaml, and
    # tidy the leading "- " so it pastes in cleanly.
    indented = "\n".join(("      " + line if line.strip() else line) for line in yaml_str.splitlines())
    if indented.lstrip().startswith("- "):
        indented = indented.replace("      - ", "      - ", 1)
    return indented


def main():
    parser = argparse.ArgumentParser(description="Export GitHub team data as repositories.yaml blocks.")
    parser.add_argument("--org", help="GitHub organisation (or set GITHUB_ORG)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="List every team in the org (slug/name/id)")
    mode.add_argument("--yaml", metavar="TEAM_SLUG", help="Export one team as a repositories.yaml block")
    parser.add_argument(
        "--set-permission",
        default="push",
        help="Permission to write into the exported block's `permission:` field (default: push) — "
        "this is NOT read from GitHub, since permission is a repo-attachment property, not a team "
        "property; set it to whatever this team's actual access level on the target repo is.",
    )
    args = parser.parse_args()

    token = get_token()
    org = get_org(args.org)
    headers = build_headers(token)

    if args.list:
        teams = list_org_teams(org, headers)
        print(f"Total teams found in {org}: {len(teams)}\n")
        for t in teams:
            print(f"Slug: {t['slug']:<35} | Name: {t['name']:<35} | ID: {t['id']}")
        return

    print(f"\n# Copy and paste the block below into repositories.yaml (under the target repo's `teams:`):\n")
    print(build_team_yaml_block(org, args.yaml, headers, args.set_permission))


if __name__ == "__main__":
    main()
