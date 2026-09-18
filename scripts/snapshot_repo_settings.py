#!/usr/bin/env python3
"""
Captures a detailed "before" snapshot of every setting the Terraform
governance module manages (repository_settings.tf, branch_rulesets.tf,
tag_protection.tf, dependabot_and_actions.tf), for a set of repos —
BEFORE this module is ever applied to them.

This is new — nothing in the original script set covered this. Purpose:
  1. A rollback reference: if anything needs reverting, this is exactly
     what to revert TO (this is what caught the spike-repo settings that
     had to be manually reverted during the demo prep — having this
     snapshot up front would have made that a copy-paste instead of a
     memory exercise).
  2. Evidence of what changes when this module is first applied to a real
     repo — diff this snapshot against a post-apply snapshot (same script,
     run again) to see exactly what moved.
  3. A sanity check before writing a repo's `settings:` override block —
     e.g. confirms whether a repo already has rulesets configured (if so,
     DO NOT let Terraform create new ones — see the module's own
     documentation on duplicate-ruleset risk; these need reconciling
     manually or via `terraform import` first).

Captures, per repo:
  - Core settings: merge strategy (squash/merge/rebase/auto-merge), branch
    auto-cleanup, default branch, visibility
  - security_and_analysis: secret scanning, secret scanning push protection,
    Dependabot security updates status (as reported inline on the repo object)
  - vulnerability_alerts: enabled/disabled (separate endpoint, 204/404 based)
  - Rulesets: every ruleset on the repo, in FULL detail (conditions + rules),
    not just the summary list — a name/target/enforcement summary alone
    isn't enough to tell whether re-applying Terraform would create a
    duplicate or genuinely match
  - Dependabot automated security fixes: enabled/disabled (this is the
    dedicated toggle — separate from the security_and_analysis field above,
    which is a status mirror of the same underlying setting on newer API
    versions; both are captured since they've historically not always agreed)
  - Actions permissions: allowed_actions mode, and the selected-actions
    detail if the mode is "selected"
  - Legacy (pre-Rulesets) branch protection on the default branch, if any
    still exists — worth knowing about even though this module doesn't
    manage it, since an old-style branch protection rule can coexist with
    (and conflict with) a new ruleset

Usage:
  python3 snapshot_repo_settings.py --repos-file epr-repos.txt --out-dir snapshots/
  python3 snapshot_repo_settings.py --from-yaml config/repositories.yaml --out-dir snapshots/
  python3 snapshot_repo_settings.py --repos-file epr-repos.txt --out-dir snapshots/ --summary-out before-state.csv
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import yaml

from github_api_common import API_ROOT, api_get, build_headers, get_org, get_token, paginated_get, write_table


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


def snapshot_repo(org: str, repo_name: str, headers: dict) -> dict:
    repo = api_get(f"{API_ROOT}/repos/{org}/{repo_name}", headers)
    if repo is None:
        return {"error": f"repo '{repo_name}' not found or not accessible"}

    default_branch = repo.get("default_branch", "main")

    # GitHub only includes merge-option fields (allow_squash_merge etc.)
    # and security_and_analysis in the repo response when the caller has
    # ADMIN access to this specific repo — otherwise they're silently
    # omitted, not returned as false. Without checking this explicitly,
    # "omitted" and "genuinely disabled" are indistinguishable, and a
    # missing-permission repo would misreport as having everything turned
    # off. `permissions.admin` on the repo object is the caller's own
    # effective permission on THIS repo, reported directly by GitHub —
    # use that as the authoritative signal rather than inferring from
    # which fields happened to come back null.
    caller_has_admin = repo.get("permissions", {}).get("admin")

    if caller_has_admin is False:
        print(
            f"  [warn] no admin access to '{repo_name}' — merge strategy, "
            f"security_and_analysis, and Dependabot status will be "
            f"UNVERIFIED, not confirmed-false. See caller_has_admin_on_repo "
            f"in the output.",
            file=sys.stderr,
        )

    snapshot = {
        "repo": repo_name,
        "default_branch": default_branch,
        "visibility": repo.get("visibility"),
        "caller_has_admin_on_repo": caller_has_admin,
        # Activity / liveness signals — none of these are admin-gated
        # (unlike most fields below), so they're reliable regardless of the
        # caller's permission level on this repo. Added specifically to
        # answer "is this repo actually still in use" — a settings/ruleset
        # audit alone can't tell you that, and a repo showing UNVERIFIED
        # everywhere else might simply be dormant rather than actually
        # risky.
        "created_at": repo.get("created_at"),
        "pushed_at": repo.get("pushed_at"),  # last code push — the real "still active" signal
        "updated_at": repo.get("updated_at"),  # includes metadata-only changes (topics, description, etc.) — less precise than pushed_at
        "size_kb": repo.get("size"),  # 0 strongly correlates with an empty repo, but branch_count below is the authoritative check
        "archived": repo.get("archived"),
        "branch_count": _get_branch_count(org, repo_name, headers),
        "merge_strategy": {
            "allow_merge_commit": repo.get("allow_merge_commit"),
            "allow_squash_merge": repo.get("allow_squash_merge"),
            "allow_rebase_merge": repo.get("allow_rebase_merge"),
            "allow_auto_merge": repo.get("allow_auto_merge"),
            "delete_branch_on_merge": repo.get("delete_branch_on_merge"),
            "squash_merge_commit_title": repo.get("squash_merge_commit_title"),
            "squash_merge_commit_message": repo.get("squash_merge_commit_message"),
            "merge_commit_title": repo.get("merge_commit_title"),
            "merge_commit_message": repo.get("merge_commit_message"),
        },
        "security_and_analysis": repo.get("security_and_analysis", {}),
        "vulnerability_alerts_enabled": _check_vulnerability_alerts(org, repo_name, headers),
        "rulesets": _get_rulesets_full(org, repo_name, headers),
        "dependabot_security_updates": _get_dependabot_security_updates(org, repo_name, headers),
        "actions_permissions": _get_actions_permissions(org, repo_name, headers),
        "legacy_branch_protection": _get_legacy_branch_protection(org, repo_name, default_branch, headers),
        # Summary-level "who owns/has access to this" pointers, meant as a
        # first-reference for prioritizing and populating repositories.yaml
        # — NOT a substitute for audit_repo_teams.py, which is still the
        # source of truth for full team membership and roles once you've
        # decided which repos are actually active.
        "repo_admins": _get_repo_admins(org, repo_name, headers),
        "teams": _get_repo_teams_brief(org, repo_name, headers),
    }
    snapshot["is_empty"] = snapshot["branch_count"] == 0
    snapshot["days_since_last_push"] = _days_since(repo.get("pushed_at"))
    return snapshot


def _get_repo_admins(org: str, repo_name: str, headers: dict) -> list[str] | None:
    """Direct (non-team) collaborators with admin permission — GitHub's own
    closest concept of a repo "owner". Requires PUSH access to list at all
    (a different, lower gate than the ADMIN gate on merge/security fields —
    see caller_has_admin_on_repo above), so this can be unverifiable even
    on repos where other fields succeeded. Returns None (not []) when the
    call fails, so an honest "we couldn't check" is never confused with a
    real "no admins configured"."""
    admins = api_get(
        f"{API_ROOT}/repos/{org}/{repo_name}/collaborators",
        headers,
        params={"affiliation": "direct", "permission": "admin", "per_page": 100},
    )
    if admins is None:
        return None
    return [a.get("login") for a in admins]


def _get_repo_teams_brief(org: str, repo_name: str, headers: dict) -> list[dict]:
    """Which team(s) have access, and at what permission — names/slugs
    only, no member expansion (that's audit_repo_teams.py's job, once this
    spreadsheet has told you which repos are worth auditing in full)."""
    teams = api_get(f"{API_ROOT}/repos/{org}/{repo_name}/teams", headers, params={"per_page": 100})
    if not teams:
        return []
    return [{"name": t.get("name"), "slug": t.get("slug"), "permission": t.get("permission")} for t in teams]


def _get_branch_count(org: str, repo_name: str, headers: dict) -> int:
    """Authoritative emptiness check — the same one the Terraform module's
    own plan-time precondition uses (see repository_settings.tf), so this
    doubles as "would this repo currently fail that precondition too"."""
    branches = paginated_get(f"{API_ROOT}/repos/{org}/{repo_name}/branches", headers)
    return len(branches or [])


def _days_since(iso_timestamp: str | None) -> int | None:
    if not iso_timestamp:
        return None
    from datetime import datetime, timezone

    pushed = datetime.strptime(iso_timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - pushed).days


def _check_vulnerability_alerts(org: str, repo_name: str, headers: dict) -> bool:
    """GET returns 204 (no body) if enabled, 404 if disabled — api_get maps
    204 to True and 404 to None, so this just normalizes None -> False."""
    result = api_get(f"{API_ROOT}/repos/{org}/{repo_name}/vulnerability-alerts", headers)
    return bool(result)


def _get_rulesets_full(org: str, repo_name: str, headers: dict) -> list[dict]:
    """The list endpoint only returns a summary (id/name/target/enforcement)
    — fetch each ruleset's full detail (conditions + rules) individually,
    since a name/target match alone doesn't tell you whether re-applying
    Terraform would produce a duplicate or a genuine match."""
    summaries = paginated_get(f"{API_ROOT}/repos/{org}/{repo_name}/rulesets", headers) or []
    full = []
    for s in summaries:
        detail = api_get(f"{API_ROOT}/repos/{org}/{repo_name}/rulesets/{s['id']}", headers)
        full.append(detail or s)
    return full


def _get_dependabot_security_updates(org: str, repo_name: str, headers: dict) -> dict:
    result = api_get(f"{API_ROOT}/repos/{org}/{repo_name}/automated-security-fixes", headers)
    if result is None:
        return {"enabled": False, "note": "endpoint returned 404 — treated as disabled"}
    return result if isinstance(result, dict) else {"enabled": True}


def _get_actions_permissions(org: str, repo_name: str, headers: dict) -> dict:
    perms = api_get(f"{API_ROOT}/repos/{org}/{repo_name}/actions/permissions", headers) or {}
    if perms.get("allowed_actions") == "selected":
        selected = api_get(f"{API_ROOT}/repos/{org}/{repo_name}/actions/permissions/selected-actions", headers)
        perms["selected_actions_detail"] = selected
    return perms


def _get_legacy_branch_protection(org: str, repo_name: str, branch: str, headers: dict):
    """Worth knowing about even though this module doesn't manage it — an
    old-style branch protection rule can coexist with, and conflict with,
    a Rulesets-based rule on the same branch. Returns None if the branch
    has no legacy protection (the common, expected case for a repo already
    migrated to, or newly onboarded onto, Rulesets)."""
    return api_get(f"{API_ROOT}/repos/{org}/{repo_name}/branches/{branch}/protection", headers)


def flatten_for_summary(snapshot: dict) -> dict:
    """One-row-per-repo flat view for a quick CSV/Excel eyeball — the full
    detail (especially ruleset rules) only lives in the per-repo JSON."""
    ms = snapshot.get("merge_strategy", {})
    sa = snapshot.get("security_and_analysis", {})
    rulesets = snapshot.get("rulesets", [])
    dep = snapshot.get("dependabot_security_updates", {})
    actions = snapshot.get("actions_permissions", {})
    unverified = snapshot.get("caller_has_admin_on_repo") is False

    def _v(value):
        # Distinguishes "we checked, it's off" from "we couldn't check" —
        # a blank/False-looking cell for an admin-gated field on a repo the
        # auditor lacks admin on is NOT the same thing as that setting
        # actually being disabled, and the earlier version of this table
        # made exactly that mistake for epr-frontend.
        if value is None and unverified:
            return "UNVERIFIED (no admin access)"
        return value

    repo_admins = snapshot.get("repo_admins")
    teams = snapshot.get("teams", [])

    return {
        "Repo": snapshot.get("repo"),
        "DefaultBranch": snapshot.get("default_branch"),
        "Visibility": snapshot.get("visibility"),
        "Archived": snapshot.get("archived"),
        "IsEmpty": snapshot.get("is_empty"),
        "BranchCount": snapshot.get("branch_count"),
        "LastPushedAt": snapshot.get("pushed_at"),
        "DaysSinceLastPush": snapshot.get("days_since_last_push"),
        "CreatedAt": snapshot.get("created_at"),
        "RepoAdmins": "; ".join(repo_admins) if repo_admins else ("UNVERIFIED (no push access)" if repo_admins is None else "-"),
        "TeamCount": len(teams),
        "TeamNames": "; ".join(t["name"] for t in teams) or "-",
        "TeamSlugs": "; ".join(t["slug"] for t in teams) or "-",
        "TeamPermissions": "; ".join(f"{t['slug']}:{t['permission']}" for t in teams) or "-",
        "CallerHasAdminOnRepo": snapshot.get("caller_has_admin_on_repo"),
        "AllowMergeCommit": _v(ms.get("allow_merge_commit")),
        "AllowSquashMerge": _v(ms.get("allow_squash_merge")),
        "AllowRebaseMerge": _v(ms.get("allow_rebase_merge")),
        "AllowAutoMerge": _v(ms.get("allow_auto_merge")),
        "DeleteBranchOnMerge": _v(ms.get("delete_branch_on_merge")),
        "SecretScanning": _v((sa.get("secret_scanning") or {}).get("status") if sa else None),
        "SecretScanningPushProtection": _v((sa.get("secret_scanning_push_protection") or {}).get("status") if sa else None),
        "VulnerabilityAlertsEnabled": snapshot.get("vulnerability_alerts_enabled"),
        "DependabotSecurityUpdatesEnabled": (
            "UNVERIFIED (no admin access)" if unverified and not dep.get("enabled") else dep.get("enabled")
        ),
        "RulesetCount": len(rulesets),
        "RulesetNames": "; ".join(r.get("name", "?") for r in rulesets) or "-",
        "ActionsAllowedMode": _v(actions.get("allowed_actions")),
        "HasLegacyBranchProtection": snapshot.get("legacy_branch_protection") is not None,
    }


def main():
    parser = argparse.ArgumentParser(description="Snapshot current repo settings/rulesets before Terraform onboarding.")
    parser.add_argument("--org", help="GitHub organisation (or set GITHUB_ORG)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--repos-file", help="Text file of repo names, one per line")
    source.add_argument("--from-yaml", help="Snapshot exactly the repos listed in this repositories.yaml")
    parser.add_argument("--out-dir", required=True, help="Directory to write one JSON file per repo into")
    parser.add_argument("--summary-out", help="Also write a flat one-row-per-repo summary table (.csv or .xlsx)")
    args = parser.parse_args()

    token = get_token()
    org = get_org(args.org)
    headers = build_headers(token)

    repo_names = load_repo_names_from_file(args.repos_file) if args.repos_file else load_repo_names_from_yaml(args.from_yaml)
    if not repo_names:
        print("No repos to snapshot.", file=sys.stderr)
        sys.exit(0)

    os.makedirs(args.out_dir, exist_ok=True)
    summary_rows = []

    for i, repo_name in enumerate(repo_names, start=1):
        print(f"Snapshotting ({i}/{len(repo_names)}): {repo_name}...", file=sys.stderr)
        snapshot = snapshot_repo(org, repo_name, headers)

        safe_name = repo_name.replace("/", "__")
        out_path = os.path.join(args.out_dir, f"{safe_name}.json")
        with open(out_path, "w") as f:
            json.dump(snapshot, f, indent=2)

        if "error" not in snapshot:
            summary_rows.append(flatten_for_summary(snapshot))
        else:
            print(f"  [warn] {snapshot['error']}", file=sys.stderr)

    print(f"\nWrote {len(repo_names)} per-repo snapshot files to {os.path.abspath(args.out_dir)}/")

    if args.summary_out:
        write_table(summary_rows, args.summary_out)


if __name__ == "__main__":
    main()
