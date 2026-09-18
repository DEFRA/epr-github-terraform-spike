# Attach teams to repositories with specified permissions (push, admin, etc.)
resource "github_team_repository" "this" {
  for_each   = local.repo_team_attachments

  repository = each.value.repository
  team_id    = github_team.this[each.value.team_slug].id
  permission = each.value.permission
}