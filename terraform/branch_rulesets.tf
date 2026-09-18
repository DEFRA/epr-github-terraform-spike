# Default-branch protection, implemented via the modern Repository Rulesets
# API rather than legacy `github_branch_protection`. Rulesets are GitHub's
# current recommended replacement: they support layered bypass actors,
# evaluate-only dry-run mode, and (at org scope) fleet-wide reuse — legacy
# branch protection is being phased out in favour of this model.
#
# NOTE: attribute names below are current as of provider v6.x. This resource
# has changed shape across major provider versions — pin your provider
# version in providers.tf and diff this file against the registry docs for
# `github_repository_ruleset` if you bump it.

resource "github_repository_ruleset" "default_branch" {
  for_each = local.repo_settings_map

  name        = "default-branch-baseline"
  repository  = each.key
  target      = "branch"
  enforcement = each.value.branch_protection.enforcement # "active" | "evaluate" (dry-run) | "disabled"

  conditions {
    ref_name {
      include = ["~DEFAULT_BRANCH"]
      exclude = []
    }
  }

  # Best practice: nobody bypasses, not even org owners — a genuine
  # break-glass path should be a documented, audited manual action, not a
  # standing bypass. If you need one, add an explicit team here, e.g.:
  #
  # bypass_actors {
  #   actor_id    = github_team.this["release-managers"].id
  #   actor_type  = "Team"
  #   bypass_mode = "always"
  # }
  dynamic "bypass_actors" {
    for_each = each.value.branch_protection.bypass_actor_team_slugs
    content {
      actor_id    = github_team.this[bypass_actors.value].id
      actor_type  = "Team"
      bypass_mode = "pull_request" # can still be required to open a PR; can't push straight to main
    }
  }

  rules {
    # --- Direct Commits to Main ---
    # Requiring a pull_request block (below) is what actually blocks direct
    # pushes to the branch for everyone not in bypass_actors — there's no
    # separate "no direct commits" toggle, it's an emergent property of
    # requiring a PR.
    creation = false # don't let people create a branch named exactly the default branch elsewhere
    update   = true  # governs direct pushes/updates to matching refs
    deletion = each.value.branch_protection.block_branch_deletion

    # --- Branch Deletions / Force Pushes ---
    non_fast_forward = each.value.branch_protection.block_force_pushes

    # --- Linear Commit History ---
    required_linear_history = each.value.branch_protection.required_linear_history

    # --- Signed commits (bonus best-practice item) ---
    required_signatures = each.value.branch_protection.require_signed_commits

    # --- Pull Request Reviews ---
    pull_request {
      required_approving_review_count   = each.value.branch_protection.required_approving_review_count
      dismiss_stale_reviews_on_push     = each.value.branch_protection.dismiss_stale_reviews
      require_code_owner_review         = each.value.branch_protection.require_code_owner_reviews
      require_last_push_approval        = each.value.branch_protection.require_last_push_approval
      required_review_thread_resolution = each.value.branch_protection.require_conversation_resolution
    }

    # --- CI/CD Status Checks ---
    # The provider requires at least 1 required_check block whenever
    # required_status_checks{} is present at all — it can't be declared
    # empty. So the whole block is conditional: repos with no CI (contexts
    # = []) get no status-check requirement at all, rather than an invalid
    # empty one.
    dynamic "required_status_checks" {
      for_each = length(each.value.branch_protection.required_status_checks.contexts) > 0 ? [1] : []
      content {
        strict_required_status_checks_policy = each.value.branch_protection.required_status_checks.strict

        dynamic "required_check" {
          for_each = each.value.branch_protection.required_status_checks.contexts
          content {
            context = required_check.value
          }
        }
      }
    }
  }
}
