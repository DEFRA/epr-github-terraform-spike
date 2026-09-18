# Brings ALREADY-EXISTING GitHub teams (marked `existing: true` in
# repositories.yaml) under Terraform management without trying to re-create
# them — that's what caused the "name already exists" / orphan-team failures
# earlier. Teams WITHOUT `existing: true` are untouched by this file and
# still follow the normal create path in teams.tf.
#
# IMPORTANT — before relying on this for a real migration:
# 1. For each existing team, populate its `members:` list in the YAML to
#    exactly mirror what's actually on GitHub right now (check via the UI
#    or API first). A mismatch doesn't corrupt anything, but it *will* show
#    up as an update/destroy on the first `plan` after import — review that
#    plan carefully before applying, don't rubber-stamp it.
# 2. Run `terraform plan` after adding these imports. A clean migration
#    shows 0 changes for anything tagged `existing: true`. Anything else
#    means the YAML doesn't yet match reality — fix the YAML, not the state.
# 3. Once imported (state has the resource), these `import` blocks are
#    no-ops on every subsequent apply — safe to leave in place, or remove
#    them post-migration to keep the codebase lean. Your call.

data "github_team" "existing" {
  for_each = local.existing_teams_map
  slug     = each.key
}

# --- Import the teams themselves ---
import {
  for_each = local.existing_teams_map
  to       = github_team.this[each.key]
  id       = data.github_team.existing[each.key].id
}

# --- Import their current memberships ---
import {
  for_each = {
    for key, m in local.team_memberships : key => m
    if try(local.all_teams_map[m.team_slug].existing, false)
  }
  to = github_team_membership.this[each.key]
  id = "${data.github_team.existing[each.value.team_slug].id}:${each.value.username}"
}

# --- Import repo attachments — only the ones that actually already exist ---
import {
  for_each = {
    for key, a in local.repo_team_attachments : key => a
    if a.attachment_existing
  }
  to = github_team_repository.this[each.key]
  id = "${data.github_team.existing[each.value.team_slug].id}:${each.value.repository}"
}
