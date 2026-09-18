# Collaborators with direct (non-team) access to the repo
data "github_collaborators" "this" {
  for_each    = toset(local.repo_names)
  owner       = local.github_org
  repository  = each.value
  affiliation = "direct"
}