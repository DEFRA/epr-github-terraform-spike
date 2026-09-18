locals {
  # 1. Load and decode the YAML configuration file
  repositories_config = yamldecode(file("${path.module}/config/repositories.yaml"))

  # GitHub Organization Name
  github_org = try(local.repositories_config.github_organisation, "ABCD")

  # Identity Terraform authenticates as. This account must never be managed
  # as an explicit github_team_membership — its access to any team should
  # come only from create_default_maintainer (at creation) or org-owner
  # rights, never from a Terraform-managed resource Terraform could later
  # destroy mid-lifecycle.
  terraform_service_account = "devenbairat"

  # Extract raw repositories list safely
  raw_repos = try(local.repositories_config.repos, [])

  # Extract list of repository names for data.tf / github_collaborators lookup
  repo_names = [for repo in local.raw_repos : repo.name]

  # 2. Extract and flatten all unique teams defined across all repositories
  all_teams_map = merge([
    for repo in local.raw_repos : {
      for team in try(repo.teams, []) : team.slug => {
        name           = team.name
        slug           = team.slug
        parent_team_id = try(team.parent_team_id, null)
        members        = try(team.members, [])
        existing       = try(team.existing, false)
      }
    }
  ]...)

  # Teams already present in GitHub — these get imported, never created.
  existing_teams_map = {
    for slug, team in local.all_teams_map : slug => team if team.existing
  }

  # 3. Flatten team memberships for github_team_membership resources.
  #    Explicitly excludes terraform_service_account.
  team_memberships = merge([
    for team_slug, team in local.all_teams_map : {
      for member in try(team.members, []) : format("%s:%s", team_slug, member.username) => {
        team_slug = team_slug
        username  = member.username
        role      = try(member.role, "member")
      }
      if member.username != local.terraform_service_account
    }
  ]...)

  # 4. Flatten repository-team permissions
  repo_team_attachments = merge([
    for repo in local.raw_repos : {
      for team in try(repo.teams, []) : format("%s:%s", repo.name, team.slug) => {
        repo       = repo.name
        repository = repo.name
        team_slug  = team.slug
        team_name  = try(team.name, team.slug)
        permission = try(team.permission, "push")
        # Whether this SPECIFIC repo↔team attachment already exists on
        # GitHub — independent of whether the team itself exists. A real,
        # pre-existing team can still be brand new to a given repo.
        attachment_existing = try(team.attachment_existing, false)
      }
    }
  ]...)

  repo_teams_flat = local.repo_team_attachments

  # 5. Direct collaborators lookup map
  direct_collaborators_by_repo = {
    for repo in local.raw_repos : repo.name => try(repo.direct_collaborators, [])
  }

  # ---------------------------------------------------------------------
  # 6. Repo baseline settings: org-wide defaults, overridable per repo.
  #    A repo's `settings:` block in the YAML only needs to specify what it
  #    wants to DIFFER from the default — everything else is inherited.
  # ---------------------------------------------------------------------

  settings_defaults = {
    branch_protection = {
      enforcement                       = "active" # "active" | "evaluate" (dry-run, reports without blocking) | "disabled"
      required_approving_review_count   = 1
      dismiss_stale_reviews             = true
      require_code_owner_reviews        = true
      require_last_push_approval        = true
      require_conversation_resolution   = true
      require_signed_commits            = false
      required_linear_history           = true
      block_force_pushes                = true
      block_branch_deletion             = true
      bypass_actor_team_slugs           = [] # e.g. ["release-managers"] for a documented break-glass path
      required_status_checks = {
        strict   = true
        contexts = ["ci/build", "ci/test"]
      }
    }
    merge_strategy = {
      allow_merge_commit           = false
      allow_squash_merge           = true
      allow_rebase_merge           = false
      allow_auto_merge             = true
      delete_branch_on_merge       = true
      squash_merge_commit_title    = "PR_TITLE"
      squash_merge_commit_message  = "PR_BODY"
    }
    tag_protection = {
      enforcement = "active" # "active" | "evaluate" | "disabled"
      patterns    = ["v*"]
    }
    actions = {
      allowed_actions  = "selected" # "all" | "local_only" | "selected"
      allowed_patterns = ["actions/*", "github/*"]
    }
    dependabot_security_updates     = true
    vulnerability_alerts            = true
    secret_scanning                 = true
    secret_scanning_push_protection = true
  }

  # Opt-in only: a repo gets NONE of the baseline settings, rulesets, tag
  # protection, Dependabot updates, or Actions permissions managed unless
  # its YAML block explicitly declares a `settings:` key — even an empty
  # `settings: {}` counts as opting in to full defaults. Teams/memberships/
  # repo-team attachments are NOT gated by this — those are driven by
  # `teams:` and remain independent, since bringing a repo's access model
  # under Terraform is a separate decision from enforcing its baseline
  # config.
  repo_settings_map = {
    for repo in local.raw_repos : repo.name => {
      branch_protection = merge(
        local.settings_defaults.branch_protection,
        try(repo.settings.branch_protection, {}),
        {
          # merge() is shallow — without this, a repo overriding
          # required_status_checks at all would silently drop `strict`
          # unless it repeated it. This re-merges just that sub-object.
          required_status_checks = merge(
            local.settings_defaults.branch_protection.required_status_checks,
            try(repo.settings.branch_protection.required_status_checks, {})
          )
        }
      )
      merge_strategy = merge(
        local.settings_defaults.merge_strategy,
        try(repo.settings.merge_strategy, {})
      )
      tag_protection = merge(
        local.settings_defaults.tag_protection,
        try(repo.settings.tag_protection, {})
      )
      actions = merge(
        local.settings_defaults.actions,
        try(repo.settings.actions, {})
      )
      dependabot_security_updates     = try(repo.settings.dependabot_security_updates, local.settings_defaults.dependabot_security_updates)
      vulnerability_alerts            = try(repo.settings.vulnerability_alerts, local.settings_defaults.vulnerability_alerts)
      secret_scanning                 = try(repo.settings.secret_scanning, local.settings_defaults.secret_scanning)
      secret_scanning_push_protection = try(repo.settings.secret_scanning_push_protection, local.settings_defaults.secret_scanning_push_protection)
    }
    if can(repo.settings) && repo.settings != null
  }
}
