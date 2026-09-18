# Automatically detects whether a team, and its attachment to a repo,
# already exist on GitHub — replacing the old `existing` / `attachment_existing`
# manual YAML flags. There's nothing to set, flip, or forget: on every
# plan, a small external script (scripts/check_existence.py) checks
# GitHub directly. If a team/attachment/membership is real, it's
# imported; if it isn't (yet), it's created normally. See that script's
# docstring for the exact protocol.
#
# Requirements this introduces:
#   - `python3` must be on PATH wherever `terraform plan`/`apply` runs
#     (including CI) — not just the Terraform binary and provider.
#   - `GITHUB_TOKEN` must be set in that same environment (already
#     required for the provider itself, so nothing new to configure).
#
# Cost: this issues a live GitHub API call for EVERY team and EVERY
# repo-team attachment, on EVERY plan — not just for new ones. At the
# current scale (a handful of repos/teams) this is negligible; if this
# module ever manages hundreds of repos, revisit whether the extra API
# load per plan is still acceptable (see the rate-limit notes in
# scripts/README.md).

data "external" "team_lookup" {
  for_each = local.all_teams_map
  program  = ["python3", "${path.module}/../scripts/check_existence.py"]
  query = {
    org  = local.github_org
    kind = "team"
    slug = each.key
  }
}

data "external" "attachment_lookup" {
  for_each = local.repo_team_attachments
  program  = ["python3", "${path.module}/../scripts/check_existence.py"]
  query = {
    org        = local.github_org
    kind       = "attachment"
    slug       = each.value.team_slug
    repository = each.value.repository
  }
}

# --- Import teams that already exist ---
import {
  for_each = {
    for slug, t in local.all_teams_map : slug => t
    if data.external.team_lookup[slug].result.exists == "true"
  }
  to = github_team.this[each.key]
  id = data.external.team_lookup[each.key].result.id
}

# --- Import memberships that are genuinely real on GitHub right now ---
# A member is imported only if BOTH: their team already exists, AND their
# username is actually in that team's live member list. A brand-new
# member added to an already-real team is correctly left for the normal
# create path — no per-member flag needed, it's derived from reality.
import {
  for_each = {
    for key, m in local.team_memberships : key => m
    if data.external.team_lookup[m.team_slug].result.exists == "true"
    && contains(
      [for x in jsondecode(data.external.team_lookup[m.team_slug].result.members_json) : x.username],
      m.username
    )
  }
  to = github_team_membership.this[each.key]
  id = "${data.external.team_lookup[each.value.team_slug].result.id}:${each.value.username}"
}

# --- Import repo attachments that already exist ---
import {
  for_each = {
    for key, a in local.repo_team_attachments : key => a
    if data.external.attachment_lookup[key].result.exists == "true"
  }
  to = github_team_repository.this[each.key]
  id = "${data.external.team_lookup[each.value.team_slug].result.id}:${each.value.repository}"
}
