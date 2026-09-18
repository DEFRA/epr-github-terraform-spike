output "repo_audit" {
  description = "Per-repo audit: which teams have access (and at what permission level), each team's members and their team role, and any collaborators with direct (non-team) access."
  value = {
    for repo_cfg in local.repositories_config.repos : repo_cfg.name => {
      teams = [
        for key, t in local.repo_teams_flat : {
          name       = t.team_name
          slug       = t.team_slug
          permission = t.permission
          members    = try(repo_cfg.teams[t.team_slug], [])
        }
        if t.repo == repo_cfg.name
      ]
      direct_collaborators = try(local.direct_collaborators_by_repo[repo_cfg.name], [])
    }
  }
}