# EPR GitHub governance scripts

Cleaned-up and consolidated replacement for the original six scripts. What
changed and why, then usage for the two jobs requested for the start of the
rollout.

## Repo layout

```
terraform/
├── *.tf
└── config/repositories.yaml    # what Terraform reads
scripts/
├── *.py                        # this directory
├── README.md                   # this file
└── output/                     # generated files land here — never committed, never source
    ├── epr-repos.txt
    ├── epr-team-audit.json / .xlsx
    ├── before-state-summary.xlsx
    └── snapshots/
```

All commands below assume you're running from inside `scripts/`, writing
into `output/` and reading the Terraform config via `../terraform/config/repositories.yaml`.

## Requirements

```bash
pip install requests pyyaml
pip install pandas openpyxl   # only needed for .xlsx output — CSV works without them
```

```bash
export GITHUB_TOKEN=ghp_xxxxxxxx   # or GH_TOKEN
export GITHUB_ORG=DEFRA            # or pass --org to every script
```

## Job 1: find EPR repos, their teams, and team members with roles

Two steps — discover, then audit:

```bash
# 1. Find every repo matching "epr" by name or topic
python3 discover_repos.py --mode match --term epr --format names > output/epr-repos.txt

# 2. For each of those repos: which teams have access (and at what
#    permission), and each team's full member list with role
python3 audit_repo_teams.py --repos-file output/epr-repos.txt \
    --json-out output/epr-team-audit.json \
    --table-out output/epr-team-audit.xlsx
```

Or in one step, skipping the intermediate file:

```bash
python3 audit_repo_teams.py --org DEFRA --term epr \
    --json-out output/epr-team-audit.json --table-out output/epr-team-audit.xlsx
```

`epr-team-audit.xlsx` is a flat, one-row-per-team table (repo, team, slug,
permission, parent team, and a semicolon-separated `username:role` member
list) — good for a quick spreadsheet review or sharing outside the team.
`epr-team-audit.json` has the same data in full nested form, and its
`members` lists can be pasted directly into `repositories.yaml` team blocks
— team/attachment existence is now detected automatically, nothing to flag.

If you already have a `repositories.yaml` and want to re-audit exactly what's
in it (this was `audit_repos.py`'s job):

```bash
python3 audit_repo_teams.py --from-yaml ../terraform/config/repositories.yaml \
    --json-out output/audit.json
```

## Job 2: snapshot current rulesets/settings as a "before" baseline

New script — nothing in the original set did this.

```bash
python3 snapshot_repo_settings.py --repos-file output/epr-repos.txt \
    --out-dir output/snapshots/ \
    --summary-out output/before-state-summary.xlsx
```

This writes one full JSON file per repo into `output/snapshots/` (merge strategy,
security/vulnerability settings, every ruleset in full detail, Dependabot
security-updates status, Actions permissions, and a check for any legacy
pre-Rulesets branch protection), plus one flat summary spreadsheet across
all repos for a quick before/after comparison.

**Run this before the first `terraform apply` against any real repo.** It's
the rollback reference if anything needs reverting, and — just as
important — it tells you up front whether a repo already has rulesets
configured manually, which must be reconciled (imported or removed) rather
than left for Terraform to create alongside them; GitHub does not enforce
unique ruleset names, so a naive apply would silently create a duplicate.

Re-run the same command after applying Terraform to a repo, diff the two
JSON files (or two rows in the summary sheet) for that repo, and you have
an exact record of what changed.