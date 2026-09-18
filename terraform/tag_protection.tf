# Tag Protection
# Prevents deletion/overwrite of release tags (e.g. semver tags like v1.2.3)
# by anyone outside the bypass list. Implemented as a "tag"-targeted
# ruleset rather than the older `github_repository_tag_protection` resource,
# so it's governed by the same bypass/enforcement model as the branch
# ruleset above, and can layer creation/update/deletion rules individually
# rather than being all-or-nothing.

resource "github_repository_ruleset" "tag_protection" {
  for_each = { for name, cfg in local.repo_settings_map : name => cfg if length(cfg.tag_protection.patterns) > 0 }

  name        = "tag-baseline"
  repository  = each.key
  target      = "tag"
  enforcement = each.value.tag_protection.enforcement # "active" | "evaluate" | "disabled"

  conditions {
    ref_name {
      include = [for p in each.value.tag_protection.patterns : "refs/tags/${p}"]
      exclude = []
    }
  }

  rules {
    creation = false # anyone with push can still create tags matching the pattern; set true to lock creation down too
    update   = true  # blocks moving/overwriting an existing tag
    deletion = true  # blocks deleting a released tag
  }
}
