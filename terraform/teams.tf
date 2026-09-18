# Create or manage teams
resource "github_team" "this" {
  for_each = local.all_teams_map

  name                       = each.value.name
  description                = "Managed via Terraform (repositories.yaml)"
  privacy                    = "closed"
  parent_team_id             = each.value.parent_team_id
  create_default_maintainer  = true
}

# Assign team memberships
resource "github_team_membership" "this" {
  for_each = local.team_memberships

  team_id  = github_team.this[each.value.team_slug].id
  username = each.value.username
  role     = each.value.role
}