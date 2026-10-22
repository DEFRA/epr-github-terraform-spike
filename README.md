# epr-github-terraform-spike

Test pipeline: can the EPR app pipelines take their Azure service connection name from
the shared `ServiceConnections` library (one variable per environment, e.g.
`ServiceConnectionDEV1`) instead of one `ServiceConnection-<env>` library per environment?

It deploys nothing. It loads `pipelines/vars/<env>.yaml` the way the app repos do, checks
every value resolved, then (optionally) signs in with the service connection and runs a
read-only `az account show`.

## Run it

1. Azure DevOps: Pipelines → New pipeline → GitHub → `DEFRA/epr-github-terraform-spike`
   → Existing YAML file → `/pipelines/service-connections-test.yaml`.
2. Run it and pick a DEV environment (DEV1 by default). On the first run, permit the
   `ServiceConnections` library and the `AZD-RWD-<env>` service connection.

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
