provider "github" {
  owner = local.github_org

  # Deliberately not setting `token` here. The provider automatically reads
  # GITHUB_TOKEN (or GITHUB_APP_ID / GITHUB_APP_INSTALLATION_ID / GITHUB_APP_PEM_FILE
  # for App-based auth) from the environment. Keeping auth out of this file
  # and out of any .tfvars means it never risks landing in version control
  # or Terraform state.
}
