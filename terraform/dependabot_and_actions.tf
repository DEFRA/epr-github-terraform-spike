# --- Dependabot Security Updates ---
# This is the "automatically open a PR to bump a vulnerable dependency"
# toggle. It's a separate switch from `vulnerability_alerts` in
# repository_settings.tf (which only controls whether alerts are raised at
# all) — top-notch orgs enable both: alert AND auto-remediate.
resource "github_repository_dependabot_security_updates" "this" {
  for_each = { for name, cfg in local.repo_settings_map : name => cfg if cfg.dependabot_security_updates }

  repository = each.key
  enabled    = true
}

# --- GitHub Actions permissions (a commonly missed topic) ---
# Best practice: don't leave Actions wide open to any public action from the
# Marketplace by default — that's a real supply-chain attack surface. Pin to
# actions your org has explicitly allowed (your own org's actions + a
# reviewed allow-list of third-party ones), or at minimum restrict to
# GitHub-authored + verified-creator actions.
resource "github_actions_repository_permissions" "this" {
  for_each = local.repo_settings_map

  repository      = each.key
  allowed_actions = each.value.actions.allowed_actions # "all" | "local_only" | "selected"

  dynamic "allowed_actions_config" {
    for_each = each.value.actions.allowed_actions == "selected" ? [1] : []
    content {
      github_owned_allowed = true
      verified_allowed     = true
      patterns_allowed     = each.value.actions.allowed_patterns
    }
  }
}
