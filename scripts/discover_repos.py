#!/usr/bin/env python3
"""
Discover repos in a GitHub organisation matching a name/topic pattern
(e.g. all "epr" repos) — Step 1 of bringing repos under the Terraform
governance module: find out what exists before writing repositories.yaml.

Four discovery modes:
  team   - repos a GitHub Team already has access to
  topic  - repos tagged with a given GitHub topic (only works if repos
           actually have topics set)
  name   - repos whose name contains a substring (works regardless of
           topics — the reliable fallback/primary check for "epr" repos)
  match  - union of name-substring AND topic match — catches repos that
           follow no naming convention but ARE tagged, and repos that
           aren't tagged but DO follow the naming convention

Auth:
  export GITHUB_TOKEN=ghp_xxxxxxxx   (needs read:org + repo metadata scope)
  export GITHUB_ORG=DEFRA            (or pass --org)

Usage:
  python3 discover_repos.py --verify
  python3 discover_repos.py --mode match --term epr
  python3 discover_repos.py --mode match --term epr --format tsv
  python3 discover_repos.py --mode match --term epr --format json --no-enrich
"""

from __future__ import annotations

import argparse
import json
import sys

from github_api_common import (
    API_ROOT,
    api_get,
    build_headers,
    get_org,
    get_token,
    paginated_get,
    verify_auth,
)


def get_team_repos(org: str, team_slug: str, headers: dict) -> list[dict]:
    repos = paginated_get(f"{API_ROOT}/orgs/{org}/teams/{team_slug}/repos", headers)
    return [_summarize(r) for r in repos]


def get_topic_repos(org: str, topic: str, headers: dict) -> list[dict]:
    repos = paginated_get(
        f"{API_ROOT}/search/repositories", headers, params={"q": f"org:{org} topic:{topic}"}
    )
    return [_summarize(r) for r in repos]


def get_name_repos(org: str, name_substring: str, headers: dict) -> list[dict]:
    repos = paginated_get(
        f"{API_ROOT}/search/repositories", headers, params={"q": f"org:{org} {name_substring} in:name"}
    )
    return [_summarize(r) for r in repos]


def get_match_repos(org: str, term: str, headers: dict) -> list[dict]:
    """Union of name-substring and topic match for the same term."""
    by_name = get_name_repos(org, term, headers)
    by_topic = get_topic_repos(org, term, headers)

    merged: dict[str, dict] = {}
    for r in by_name + by_topic:
        key = r["name"]
        if key not in merged:
            merged[key] = r
        else:
            existing_topics = set(merged[key]["topics"])
            existing_topics.update(r["topics"])
            merged[key]["topics"] = sorted(existing_topics)
    return list(merged.values())


def _summarize(r: dict) -> dict:
    full_name = r["full_name"]
    return {
        # Bare repo name — matches the convention used everywhere else
        # (repositories.yaml, the Terraform module, audit_repo_teams.py's
        # --repos-file input). full_name is kept separately below for the
        # repo-scoped API calls in this file, which need owner/repo.
        "name": full_name.split("/")[-1],
        "full_name": full_name,
        "visibility": r.get("visibility", "unknown"),
        "topics": r.get("topics", []),
        "archived": r.get("archived", False),
        "url": r.get("html_url", ""),
    }


def get_repo_team(org: str, full_name: str, headers: dict) -> tuple[str, str]:
    """Returns (team_name, one_member) for the first team (alphabetically)
    with access to the repo, or ('-', '-') if none. This is a quick "who
    do I ask" pointer, not a full audit — use audit_repo_teams.py for the
    complete team/member/permission breakdown."""
    teams = api_get(f"{API_ROOT}/repos/{full_name}/teams", headers, params={"per_page": 100})
    if not teams:
        return "-", "-"
    chosen = sorted(teams, key=lambda t: t.get("name", "").lower())[0]
    team_slug = chosen.get("slug")
    member_name = "-"
    if team_slug:
        members = api_get(
            f"{API_ROOT}/orgs/{org}/teams/{team_slug}/members", headers, params={"per_page": 1}
        )
        if members:
            member_name = members[0].get("login", "-")
    return chosen.get("name") or "-", member_name


def get_repo_contact(full_name: str, headers: dict) -> tuple[str, str]:
    """Prefers a repo admin; falls back to the most recent commit's author
    on the default branch as a point of contact."""
    admins = api_get(
        f"{API_ROOT}/repos/{full_name}/collaborators", headers, params={"permission": "admin", "per_page": 1}
    )
    if admins:
        return admins[0].get("login", "-"), "admin"

    commits = api_get(f"{API_ROOT}/repos/{full_name}/commits", headers, params={"per_page": 1})
    if commits:
        commit = commits[0]
        author = commit.get("author") or {}
        if author.get("login"):
            return author["login"], "last contributor"
        commit_author = commit.get("commit", {}).get("author", {})
        name = commit_author.get("name") or commit_author.get("email")
        if name:
            return name, "last contributor"
    return "-", "-"


def enrich_repos(org: str, repos: list[dict], headers: dict) -> None:
    total = len(repos)
    for i, r in enumerate(repos, start=1):
        print(f"Fetching team/contact details ({i}/{total}): {r['name']}...", file=sys.stderr)
        r["team"], r["team_member"] = get_repo_team(org, r["full_name"], headers)
        r["contact"], r["contact_type"] = get_repo_contact(r["full_name"], headers)


def main():
    parser = argparse.ArgumentParser(description="Find GitHub org repos matching a pattern.")
    parser.add_argument("--org", help="GitHub organisation (or set GITHUB_ORG)")
    parser.add_argument("--mode", choices=["team", "topic", "name", "match"])
    parser.add_argument("--team", help="Team slug (for --mode team)")
    parser.add_argument("--topic", help="Topic to search for (for --mode topic)")
    parser.add_argument("--name", help="Substring to match in repo names (for --mode name)")
    parser.add_argument("--term", help="Term to match against name OR topic (for --mode match)")
    parser.add_argument("--format", choices=["table", "json", "names", "tsv"], default="table")
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument(
        "--verify", action="store_true", help="Run auth/access diagnostics instead of a search."
    )
    parser.add_argument(
        "--no-enrich", action="store_true", help="Skip team/contact lookups — fast, name/topic only."
    )
    args = parser.parse_args()

    token = get_token()
    org = get_org(args.org)
    headers = build_headers(token)

    if args.verify:
        verify_auth(org, headers)
        return

    if not args.mode:
        parser.error("--mode is required unless using --verify")
    required = {"team": args.team, "topic": args.topic, "name": args.name, "match": args.term}
    if not required[args.mode]:
        parser.error(f"--{'team' if args.mode == 'team' else args.mode} is required for --mode {args.mode}")

    repos = {
        "team": lambda: get_team_repos(org, args.team, headers),
        "topic": lambda: get_topic_repos(org, args.topic, headers),
        "name": lambda: get_name_repos(org, args.name, headers),
        "match": lambda: get_match_repos(org, args.term, headers),
    }[args.mode]()

    if not args.include_archived:
        repos = [r for r in repos if not r["archived"]]
    repos = sorted(repos, key=lambda r: r["name"].lower())

    if not repos:
        print("No matching repos found.", file=sys.stderr)
        sys.exit(0)

    if not args.no_enrich:
        enrich_repos(org, repos, headers)
    else:
        for r in repos:
            r["team"] = r["team_member"] = r["contact"] = r["contact_type"] = "-"

    _print_results(repos, args.format)
    print(f"\n{len(repos)} repos found", file=sys.stderr)


def _print_results(repos: list[dict], fmt: str) -> None:
    if fmt == "names":
        for r in repos:
            print(r["name"])
    elif fmt == "json":
        print(json.dumps(repos, indent=2))
    elif fmt == "tsv":
        print("Repo\tVisibility\tTopics\tTeam\tTeamMember\tContact\tContactType\tArchived\tURL")
        for r in repos:
            topics_str = ";".join(r["topics"]) if r["topics"] else "-"
            print(
                f"{r['name']}\t{r['visibility']}\t{topics_str}\t{r['team']}\t{r['team_member']}\t"
                f"{r['contact']}\t{r['contact_type']}\t{r['archived']}\t{r['url']}"
            )
    else:
        print(
            f"{'REPO':<45} {'VISIBILITY':<12} {'TOPICS':<15} {'TEAM':<15} "
            f"{'TEAM MEMBER':<15} {'CONTACT':<15} {'CONTACT TYPE':<16} ARCHIVED"
        )
        for r in repos:
            topics_str = ",".join(r["topics"]) if r["topics"] else "-"
            print(
                f"{r['name']:<45} {r['visibility']:<12} {topics_str:<15} {r['team']:<15} "
                f"{r['team_member']:<15} {r['contact']:<15} {r['contact_type']:<16} {r['archived']}"
            )


if __name__ == "__main__":
    main()
