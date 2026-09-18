# EPR GitHub Governance

Terraform module + companion Python scripts for managing GitHub team access
and repository governance (branch/tag rulesets, merge strategy, Dependabot,
Actions permissions) for EPR repositories.

Full documentation: see Confluence — *GitHub Governance — Terraform Module*.

## Layout

```
terraform/
├── *.tf                    # the module itself
└── config/repositories.yaml   # source of truth — which repos/teams are managed
scripts/
├── *.py                    # discovery/audit tooling — see scripts/README.md
└── output/                 # generated reports — not committed
```

## Quick start

```bash
export GITHUB_TOKEN=<token>

cd terraform
terraform init
terraform plan
```

Before onboarding a new repo, run the audit/snapshot scripts first — see
`scripts/README.md`.