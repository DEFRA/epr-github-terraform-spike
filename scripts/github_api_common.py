#!/usr/bin/env python3
"""
Shared GitHub REST API helpers for the DEFRA EPR governance scripts.

Centralizes what was previously duplicated (with small inconsistencies)
across find_team_repos.py, audit_repos.py, get_team_members.py,
get_repo_teams.py, and get_org_teams.py:

  - token / org resolution from the environment
  - a consistent, modern auth header (Bearer + API version pin)
  - Link-header pagination
  - a "best-effort GET" that distinguishes "genuinely not found" (404) from
    "couldn't tell" (403/network error) rather than treating both as empty
  - team-member-with-role fetching via the ?role= filter (2 calls per team,
    regardless of team size — some of the original scripts did this via a
    per-member membership lookup, which is N+1 calls per team)
  - CSV/Excel table export

Every other script in this directory imports from here. Keep it dependency-
light: only `requests` is required for API calls; `pandas`/`openpyxl` are
optional, only needed for .xlsx export.
"""

from __future__ import annotations

import csv
import os
import sys

import requests

API_ROOT = "https://api.github.com"


def get_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print(
            "Error: GITHUB_TOKEN (or GH_TOKEN) environment variable is not set.",
            file=sys.stderr,
        )
        sys.exit(1)
    return token


def get_org(default: str | None = None) -> str:
    """Resolve org from --org (passed as `default` by the caller's argparse
    result) or GITHUB_ORG env var. No script in this directory hardcodes an
    org name anymore — that was a real footgun in the originals (get_org_teams.py,
    get_repo_teams.py, get_team_members.py all hardcoded "DEFRA" with a
    "# Replace with your actual org name" comment, which is exactly the
    kind of thing that gets forgotten when this is reused for another org
    or environment)."""
    org = default or os.environ.get("GITHUB_ORG")
    if not org:
        print(
            "Error: no org specified. Pass --org, or set the GITHUB_ORG env var.",
            file=sys.stderr,
        )
        sys.exit(1)
    return org


def build_headers(token: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _safe_message(resp: requests.Response) -> str:
    try:
        return resp.json().get("message", "")
    except Exception:
        return resp.text[:200]


def _die_on_fatal_errors(resp: requests.Response) -> None:
    """401/403 abort the whole run — these mean the token itself is the
    problem, not any single repo/team, so retrying per-item is pointless."""
    if resp.status_code == 401:
        print(
            "Error: 401 Unauthorized — token is invalid, expired, or not "
            "authorized for SSO on this org.",
            file=sys.stderr,
        )
        sys.exit(1)
    if resp.status_code == 403:
        msg = _safe_message(resp)
        kind = "rate-limited" if "rate limit" in msg.lower() else "Forbidden"
        print(f"Error: 403 {kind} — {msg}", file=sys.stderr)
        sys.exit(1)


def paginated_get(url: str, headers: dict, params: dict | None = None) -> list:
    """Follows GitHub's Link-header pagination and returns the combined
    item list. Handles both the search endpoints (which wrap results in an
    "items" key) and plain-list endpoints transparently."""
    results: list = []
    params = dict(params or {})
    params.setdefault("per_page", 100)

    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        _die_on_fatal_errors(resp)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("items", data) if isinstance(data, dict) else data)
        url = resp.links.get("next", {}).get("url")
        params = None  # subsequent 'next' URLs already carry the query params
    return results


def api_get(url: str, headers: dict, params: dict | None = None):
    """Single best-effort GET for one resource (not a list).

    Returns:
      - parsed JSON on 2xx
      - True on 204 (some endpoints, e.g. vulnerability-alerts, signal
        "enabled" via 204 + no body)
      - None on 404 (genuinely doesn't exist / not configured — e.g. a repo
        with no rulesets, or vulnerability alerts disabled)
      - None on 401/403/any other failure too, but ALWAYS with a [warn] to
        stderr first — this is deliberately not silent, since a permission
        gap and a real empty result look identical to the caller otherwise.

    Deliberately NEVER aborts the run, unlike paginated_get. This function
    is used for per-repo/per-resource "enrichment" lookups (contact info,
    admin collaborators, vulnerability-alert status, individual rulesets)
    across potentially hundreds of repos — some GitHub endpoints 403 based
    on the token's permission on that SPECIFIC repo, not on the token's
    overall validity (e.g. `GET .../collaborators` returns "Must have push
    access to view repository collaborators" for any repo you're not a
    direct collaborator/admin on, even with a fully-scoped, perfectly valid
    org token). Treating that as fatal would kill a 100+ repo audit on the
    first repo you don't personally have push access to — exactly the
    regression this function used to have. Only the primary listing calls
    in paginated_get (list org teams, search repos, list team members) —
    where a 401/403 really does mean something systemic — abort the run.
    """
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.RequestException as e:
        print(f"  [warn] request failed for {url}: {e}", file=sys.stderr)
        return None

    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        print(f"  [warn] {resp.status_code} on {url}: {_safe_message(resp)}", file=sys.stderr)
        return None
    if resp.status_code == 204:
        return True  # some endpoints (e.g. vulnerability-alerts) signal "enabled" via 204 + no body
    return resp.json()


def get_team_members_with_roles(org: str, team_slug: str, headers: dict) -> list[dict]:
    """Returns [{'username': ..., 'role': 'maintainer'|'member'}, ...].

    Uses the ?role= filter (2 API calls total, independent of team size).
    Neither the github_team nor github_repository_teams Terraform data
    source exposes per-member role — this is the same gap team_roles.py
    (the Terraform `external` provider script) exists to work around; this
    function is the shared implementation both that script and the plain
    audit scripts should use, rather than each re-deriving it slightly
    differently (get_team_members.py did this via a per-member membership
    lookup instead — correct, but N+1 calls per team).
    """
    members = []
    for role in ("maintainer", "member"):
        url = f"{API_ROOT}/orgs/{org}/teams/{team_slug}/members"
        for m in paginated_get(url, headers, params={"role": role}):
            members.append({"username": m["login"], "role": role})
    return members


def verify_auth(org: str, headers: dict) -> None:
    """Diagnostic check: confirms the token is valid, whose it is, whether
    it can see the org, and current rate-limit status. Run this first, or
    whenever a script unexpectedly returns nothing — it's much faster than
    guessing whether the problem is auth, scope, or a genuinely empty
    result."""
    print("Checking authentication...", file=sys.stderr)

    resp = requests.get(f"{API_ROOT}/user", headers=headers)
    if resp.status_code == 401:
        print("FAIL: Token is invalid or expired (401 on /user).", file=sys.stderr)
        sys.exit(1)
    resp.raise_for_status()
    print(f"OK: Token is valid, authenticated as '{resp.json().get('login')}'.", file=sys.stderr)

    resp = requests.get(f"{API_ROOT}/orgs/{org}", headers=headers)
    if resp.status_code == 404:
        print(f"FAIL: Org '{org}' not found or not visible to this token (404).", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 403:
        print(
            f"FAIL: Forbidden accessing org '{org}' — token may lack org access, "
            f"or needs SSO authorization. ({_safe_message(resp)})",
            file=sys.stderr,
        )
        sys.exit(1)
    resp.raise_for_status()
    print(f"OK: Can see org '{org}'.", file=sys.stderr)

    resp = requests.get(f"{API_ROOT}/user/memberships/orgs/{org}", headers=headers)
    if resp.status_code == 200:
        m = resp.json()
        print(f"OK: Membership in '{org}' — state: {m.get('state')}, role: {m.get('role')}.", file=sys.stderr)
    else:
        print(
            f"NOTE: Could not confirm membership state in '{org}' (status "
            f"{resp.status_code}) — token may be an outside collaborator, not an org member.",
            file=sys.stderr,
        )

    resp = requests.get(f"{API_ROOT}/rate_limit", headers=headers)
    if resp.status_code == 200:
        core = resp.json().get("resources", {}).get("core", {})
        print(f"OK: Rate limit — core: {core.get('remaining')}/{core.get('limit')}.", file=sys.stderr)

    print("\nVerification complete.", file=sys.stderr)


def write_table(rows: list[dict], path: str, fieldnames: list[str] | None = None) -> None:
    """Writes rows to .csv or .xlsx based on the path extension. Excel
    export needs pandas + openpyxl; falls back to CSV with a warning if
    they're not installed, rather than failing the whole run."""
    if not rows:
        print("No rows to write.", file=sys.stderr)
        return
    fieldnames = fieldnames or list(rows[0].keys())

    if path.endswith(".xlsx"):
        try:
            import pandas as pd

            pd.DataFrame(rows, columns=fieldnames).to_excel(path, index=False, engine="openpyxl")
            print(f"Wrote {len(rows)} rows to {os.path.abspath(path)}")
            return
        except ImportError:
            print("pandas/openpyxl not installed — falling back to CSV.", file=sys.stderr)
            path = path.rsplit(".", 1)[0] + ".csv"

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {os.path.abspath(path)}")
