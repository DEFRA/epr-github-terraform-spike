# Manage a constrained set of settings on repos that already exist and are
# CREATED BY A DIFFERENT TEAM. We import each repo and only take ownership of
# the attributes listed explicitly below (merge strategy, branch cleanup,
# vulnerability alerts, secret scanning). Everything else the creating team
# owns (description, topics, visibility, has_issues/wiki, default_branch,
# etc.) is protected via lifecycle.ignore_changes so this module can never
# drift or fight them on those fields.

import {
  for_each = local.repo_settings_map
  to       = github_repository.this[each.key]
  id       = each.key
}

# Guards against applying rulesets to a repo that has no branches yet
# (freshly created, never initialized). A ruleset's `creation = false` rule
# means the FIRST push that creates the default branch bypasses PR review,
# status checks, everything — the guardrails only start protecting from
# the second push onward. This can't be fixed in Terraform (branches can
# only be created on a non-empty repo, and `auto_init` only works at
# repository CREATE time, which is out of scope here) — so instead of
# applying rulesets that silently don't protect anything yet, fail loudly
# and tell whoever's running this to get the repo initialized first.
data "github_repository_branches" "this" {
  for_each   = local.repo_settings_map
  repository = each.key
}

resource "github_repository" "this" {
  for_each = local.repo_settings_map

  name = each.key

  # --- Merge Strategies ---
  # Best practice: squash-only. One commit per PR on the default branch,
  # full history preserved on the branch itself via the PR, no noisy merge
  # commits, no rebase-induced force-push confusion for reviewers.
  allow_merge_commit = each.value.merge_strategy.allow_merge_commit
  allow_squash_merge = each.value.merge_strategy.allow_squash_merge
  allow_rebase_merge = each.value.merge_strategy.allow_rebase_merge
  allow_auto_merge   = each.value.merge_strategy.allow_auto_merge

  squash_merge_commit_title   = each.value.merge_strategy.squash_merge_commit_title
  squash_merge_commit_message = each.value.merge_strategy.squash_merge_commit_message

  # --- Branch Auto-Cleanup ---
  # Deletes the head branch automatically once a PR merges. Keeps the repo's
  # branch list free of merged clutter without relying on humans remembering.
  delete_branch_on_merge = each.value.merge_strategy.delete_branch_on_merge

  # --- Dependabot / vulnerability alerts ---
  # Managed via the dedicated github_repository_vulnerability_alerts
  # resource below, not this argument — see the note on ignore_changes.

  # --- Secret scanning (requires GitHub Advanced Security on private repos;
  # free on public repos). Comment this block out if GHAS isn't licensed for
  # your org yet — leaving it in without a license will error on apply. ---
  security_and_analysis {
    secret_scanning {
      status = each.value.secret_scanning ? "enabled" : "disabled"
    }
    secret_scanning_push_protection {
      status = each.value.secret_scanning_push_protection ? "enabled" : "disabled"
    }
  }

  lifecycle {
    precondition {
      condition     = length(data.github_repository_branches.this[each.key].branches) > 0
      error_message = "Repository '${each.key}' has no branches yet (never initialized). Rulesets can't meaningfully protect a branch that doesn't exist — the first push would create it bypassing all rules. Ask the repo owner to initialize it (e.g. tick 'Add a README file') before running this module against it."
    }

    ignore_changes = [
      description,
      homepage_url,
      topics,
      visibility,
      has_issues,
      has_projects,
      has_wiki,
      has_downloads,
      has_discussions,
      archived,
      default_branch,
      auto_init,
      gitignore_template,
      license_template,
      pages,
      template,
      # vulnerability_alerts was removed from this resource's config above
      # (deprecated here; managed by github_repository_vulnerability_alerts
      # instead). Without this line, Terraform would treat the attribute as
      # unset and plan to reset it to the schema default (false) on every
      # repo — silently disabling alerts. This keeps whatever value is
      # already in state, permanently ignoring drift on it here.
      vulnerability_alerts,
    ]
  }
}

# --- Vulnerability alerts (dependency graph), as its own resource ---
# Presence of this resource = enabled; there's no separate boolean — so it's
# only created for repos where repo_settings_map says vulnerability_alerts
# should be on. A repo that explicitly wants this off simply has no
# instance of this resource (nothing to destroy/toggle either way).
resource "github_repository_vulnerability_alerts" "this" {
  for_each = { for name, cfg in local.repo_settings_map : name => cfg if cfg.vulnerability_alerts }

  repository = each.key
}
