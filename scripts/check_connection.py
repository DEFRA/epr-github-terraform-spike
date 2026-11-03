"""Read-only check of everything the EPR pipelines use one environment's service connection for.

Run by pipelines/service-connection-check.yaml inside AzureCLI@2 (signed in as the connection,
addSpnToEnvironment: true). Reads pipelines/resources/<ENV>.txt (from scripts/list_apps.py) and
checks, without changing anything:

  Library     ServiceConnection<ENV> in the ServiceConnections library is the -APP connection
  Repo vars   no repo's vars file still names a connection directly
  Apps        every repo's web/function app is visible (deploy, restart) and the connection's
              roles on it allow what the deploy templates do
  Registry    Azure AD login works (no admin-user fallback) and the roles allow pull (SQL
              migration), import (Copy Image, TST/PRE) and push (CI, DEV4)
  Key Vault   the SQL admin secret can be read (docker-run-sql-migration.yaml)
  Storage     shutter page $web and B2C styling containers can be used with --auth-mode login

Prints one line per check, raises a pipeline error for each MISSING, and writes a markdown
summary (shown on the run's summary page). Never prints secret values.
"""
import collections
import fnmatch
import json
import os
import subprocess
import sys

ENV = os.environ["ENV"]
CONNECTION = os.environ["CONNECTION"]
EXPECTED = os.environ["EXPECTED_CONNECTION"]
LIBRARY_VALUE = os.environ["LIBRARY_VALUE"]
SPN = os.environ.get("servicePrincipalId", "")
SUMMARY = os.environ["SUMMARY_FILE"]
RESOURCES = os.path.join(os.path.dirname(__file__), "..", "pipelines", "resources", f"{ENV}.txt")

# Where the shutter page and B2C styling upload (no other environment has them, PRD1 not tested).
STORAGE = {
    "DEV1": ("devrwddbssa1402", {"$web": "epr-web-shutter-screen-pages"}),
    "TST1": ("tstrwddbssa1402", {"$web": "epr-web-shutter-screen-pages",
                                 "b2c-styling-files": "epr-rpd-azure-ad-b2c-styling"}),
    "PRE1": ("prerwddbssa1402", {"$web": "epr-web-shutter-screen-pages",
                                 "epr-account-styling": "epr-rpd-azure-ad-b2c-styling"}),
}
# What epr-webapps-code-deploy-templates does to each app: get-current-dockertag (read),
# AzureRmWebAppDeployment@4 webAppContainer (config write, publishing credentials), restart.
APP_ACTIONS = [
    "Microsoft.Web/sites/read",
    "Microsoft.Web/sites/config/write",
    "Microsoft.Web/sites/config/list/action",
    "Microsoft.Web/sites/restart/action",
]

rows = []


def row(status, area, resource, needed_by, detail):
    rows.append((status, area, resource, needed_by, detail))
    print(f"{status:8} {area:10} {resource:32} {detail}")
    if status == "MISSING":
        print(f"##vso[task.logissue type=error]{area} {resource}: {detail} (needed by {needed_by})")
    elif status == "WARN":
        print(f"##vso[task.logissue type=warning]{area} {resource}: {detail}")


def az(*args):
    r = subprocess.run(["az", *args, "--only-show-errors", "-o", "json"], capture_output=True, text=True)
    out = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
    return r.returncode, out, (r.stderr or "").strip()


def short(err):
    for line in err.splitlines():
        line = line.strip()
        if line and not line.startswith("WARNING"):
            return line[:200]
    return err[:200]


def join(names, limit=4):
    names = sorted(set(names))
    return ", ".join(names[:limit]) + (f" +{len(names) - limit} more" if len(names) > limit else "")


_definitions = {}


def permissions(scope):
    """(actions, notActions, dataActions, notDataActions, role names) the connection has at scope,
    or None if its role assignments cannot be read."""
    rc, assignments, err = az("role", "assignment", "list", "--assignee", SPN, "--scope", scope, "--include-inherited")
    if rc != 0 or assignments is None:
        return None
    perms = ([], [], [], [], [])
    for a in assignments:
        name = a["roleDefinitionName"]
        if name not in _definitions:
            _, d, _ = az("role", "definition", "list", "--name", name)
            _definitions[name] = (d or [{}])[0].get("permissions", [])
        for p in _definitions[name]:
            perms[0].extend(p.get("actions", []))
            perms[1].extend(p.get("notActions", []))
            perms[2].extend(p.get("dataActions", []))
            perms[3].extend(p.get("notDataActions", []))
        perms[4].append(name)
    return perms


def allowed(perms, action, data=False):
    grants, denies = (perms[2], perms[3]) if data else (perms[0], perms[1])
    match = lambda patterns: any(fnmatch.fnmatch(action.lower(), p.lower()) for p in patterns)
    return match(grants) and not match(denies)


# Resources the repos' vars files name for this environment: repo, file, key, value
resources = [l.split() for l in open(RESOURCES) if l.strip()]

print(f"Environment {ENV}, connection {CONNECTION}\n")

# Library
if LIBRARY_VALUE == EXPECTED:
    row("OK", "Library", f"ServiceConnection{ENV}", "all repos", f"= {LIBRARY_VALUE}")
else:
    row("WARN", "Library", f"ServiceConnection{ENV}", "all repos",
        f"= {LIBRARY_VALUE}, not {EXPECTED} - switch once everything below is OK")
if CONNECTION != LIBRARY_VALUE:
    row("INFO", "Library", CONNECTION, "-", "tested directly (appConnection), not the library value")

# Repo vars that still name a connection instead of taking it from the library
for repo, file, key, value in resources:
    if key in ("azureSubscription", "acr.Subscription") and not value.startswith("$(ServiceConnection"):
        row("WARN", "Repo vars", f"{key} = {value}", repo, f"{file} does not use the library")

# Apps
rc, sites, err = az("resource", "list", "--resource-type", "Microsoft.Web/sites", "--query", "[].{name:name, rg:resourceGroup, id:id}")
visible = {s["name"].lower(): s for s in sites or []}
by_rg = collections.defaultdict(list)
for repo, file, key, app in resources:
    if not key.lower().endswith("servicename"):
        continue
    site = visible.get(app.lower())
    if site:
        by_rg[site["rg"]].append((repo, site))
    else:
        row("MISSING", "App", app, repo, "not visible to the connection - deploy and restart fail")
for rg, apps in sorted(by_rg.items()):
    repos = [r for r, _ in apps]
    perms = permissions(apps[0][1]["id"])
    if perms is None:
        row("WARN", "App", f"{rg} ({len(apps)} apps)", join(repos), "cannot read the connection's role assignments here")
        continue
    lacking = [a for a in APP_ACTIONS if not allowed(perms, a)]
    if lacking:
        row("MISSING", "App", f"{rg} ({len(apps)} apps)", join(repos),
            f"roles {join(perms[4])} lack {', '.join(lacking)}")
    else:
        row("OK", "App", f"{rg} ({len(apps)} apps)", join(repos), f"deploy + restart via {join(perms[4])}")

# Registry
registries = collections.defaultdict(list)
for repo, file, key, value in resources:
    if key == "acr.azureContainerRegistryName":
        registries[value.lower()].append(repo)
for acr, repos in sorted(registries.items()):
    login = subprocess.run(["az", "acr", "login", "--name", acr], capture_output=True, text=True)
    text = (login.stdout + login.stderr)
    if login.returncode != 0:
        row("MISSING", "Registry", acr, join(repos), f"az acr login failed: {short(login.stderr)}")
    elif "admin" in text.lower() or "401" in text:
        row("MISSING", "Registry", acr, join(repos), "Azure AD login refused (401) - only works through the admin user")
    else:
        row("OK", "Registry", acr, join(repos), "Azure AD login")
    rc, reg, err = az("acr", "show", "--name", acr, "--query", "{id:id}")
    if rc != 0:
        row("MISSING", "Registry", acr, join(repos), f"cannot read the registry (Microsoft.ContainerRegistry/registries/read): {short(err)}")
        continue
    perms = permissions(reg["id"])
    if perms is None:
        row("WARN", "Registry", acr, join(repos), "cannot read the connection's role assignments here")
        continue
    needs = {"pull (SQL migration)": ("Microsoft.ContainerRegistry/registries/pull/read",
                                      "Microsoft.ContainerRegistry/registries/repositories/content/read")}
    if not ENV.startswith("DEV"):
        needs["import (Copy Image)"] = ("Microsoft.ContainerRegistry/registries/importImage/action", None)
    if ENV == "DEV4":
        needs["push (CI)"] = ("Microsoft.ContainerRegistry/registries/push/write",
                              "Microsoft.ContainerRegistry/registries/repositories/content/write")
    for what, (action, data_action) in needs.items():
        if allowed(perms, action) or (data_action and allowed(perms, data_action, data=True)):
            row("OK", "Registry", acr, join(repos), f"{what} via {join(perms[4])}")
        else:
            hint = "" if "import" in what else " (e.g. AcrPull / AcrPush)"
            row("MISSING", "Registry", acr, join(repos), f"{what}: no {action}{hint}")

# Key Vault (SQL migrations read the admin login with the app's connection)
vaults = collections.defaultdict(list)
for repo, file, key, value in resources:
    if key == "kvName":
        vaults[value.upper()].append(repo)
for kv, repos in sorted(vaults.items()):
    rc, _, err = az("keyvault", "secret", "show", "--vault-name", kv, "--name", "RWDSql-AdminLogin", "--query", "id")
    if rc == 0:
        row("OK", "Key Vault", kv, join(repos), "can read RWDSql-AdminLogin")
    elif "SecretNotFound" in err:
        row("WARN", "Key Vault", kv, join(repos), "RWDSql-AdminLogin does not exist in this vault")
    elif "Forbidden" in err or "not authorized" in err.lower() or "does not have" in err.lower():
        row("MISSING", "Key Vault", kv, join(repos), "cannot read secrets (Key Vault Secrets User or a get access policy)")
    else:
        row("WARN", "Key Vault", kv, join(repos), f"could not check: {short(err)}")

# Storage (shutter page, B2C styling): uploads use --auth-mode login
if ENV in STORAGE:
    account, containers = STORAGE[ENV]
    for container, repo in containers.items():
        rc, _, err = az("storage", "blob", "list", "--container-name", container, "--account-name", account,
                        "--auth-mode", "login", "--num-results", "1", "--query", "[].name")
        if rc == 0:
            row("OK", "Storage", f"{account}/{container}", repo, "Azure AD access (Storage Blob Data Contributor)")
        else:
            row("MISSING", "Storage", f"{account}/{container}", repo, f"no Azure AD access (Storage Blob Data Contributor): {short(err)}")

# Summary
missing = sum(r[0] == "MISSING" for r in rows)
warn = sum(r[0] == "WARN" for r in rows)
ok = sum(r[0] == "OK" for r in rows)
headline = f"{ENV} {CONNECTION}: {missing} missing, {warn} warnings, {ok} OK"
print(f"\n##[section]{headline}")
print(f"##vso[build.updatebuildnumber]{os.environ.get('BUILD_BUILDID', '')} {ENV} {missing} missing {warn} warnings")
order = {"MISSING": 0, "WARN": 1, "INFO": 2, "OK": 3}
with open(SUMMARY, "w") as f:
    f.write(f"### {headline}\n\n| Result | Area | Resource | Needed by | Detail |\n|---|---|---|---|---|\n")
    for status, area, resource, needed_by, detail in sorted(rows, key=lambda r: (order[r[0]], r[1])):
        cell = lambda s: str(s).replace("|", "\\|")
        f.write(f"| {'**MISSING**' if status == 'MISSING' else status} | {area} | {cell(resource)} | {cell(needed_by)} | {cell(detail)} |\n")
print(f"##vso[task.uploadsummary]{SUMMARY}")
sys.exit(1 if missing else 0)
