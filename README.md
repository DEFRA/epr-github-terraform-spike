# epr-github-terraform-spike

Test pipeline: can the EPR app pipelines take their Azure service connection name from
the shared `ServiceConnections` library (one variable per environment, e.g.
`ServiceConnectionDEV1`) instead of one `ServiceConnection-<env>` library per environment?

It deploys nothing and changes nothing. It loads `pipelines/vars/<env>.yaml`
the way the app repos do, checks every value resolved and `azureSubscription` is
`AZD-RWD-<env>-APP`, then uses `$(azureSubscription)` as a task's service connection and runs
`az account show`, which should show the environment's subscription (e.g. `AZD-RWD-DEV1`).
It then checks, read-only, what the app pipelines use the connection for
(epr-backend-account-microservice's resources): finding the web app to deploy/restart,
logging in to the container registry, reading the SQL secret from Key Vault, and reading
the shared template secrets with `AZD-RWD-DEV1-APP`. Failures in these are permissions -
pass them to the team that manages the service connections.

## Run it

1. Azure DevOps: Pipelines → New pipeline → GitHub → `DEFRA/epr-github-terraform-spike`
   → Existing YAML file → `/pipelines/service-connections-test.yaml`.
2. Run it and pick a lower environment (DEV1 by default; DEV1-4, DEV7-9, DEV14, DEV15 or TST1). On the first run, permit the
   `ServiceConnections` library and the `AZD-RWD-<env>-APP` service connection.

## Result

- Both steps green: the shared library works - each app repo's vars file can change from
  `- group: ServiceConnection-DEV1` to
  ```yaml
  - group: ServiceConnections
  - name: azureSubscription
    value: $(ServiceConnectionDEV1)
  ```
- The sign-in step fails with "service connection $(ServiceConnectionDEV1) could not be
  found": Azure DevOps does not resolve a variable that points to another variable for
  service connections - keep one library per environment.

## Other test pipelines (all read-only)

- `pipelines/backend-account-test.yaml` - loads epr-backend-account-microservice's converted
  vars files (branch `chore/ELSP-371-serviceconnections-library`) and checks everything its
  pipelines use the connection for.
- `pipelines/all-apps-test.yaml` - checks the `AZD-RWD-<env>-APP` connection can see every web
  and function app the EPR repos deploy to in that environment. The app list per environment
  (`pipelines/apps/<env>.txt`) is generated from the repos' vars files:
  `python3 scripts/list_apps.py ~/GitHub <repo> ...` (repos without a vars file for an
  environment are listed in `pipelines/apps/MISSING.txt`).
