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
import textwrap

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

# Repos left out: not part of the -APP move (apps do not exist; archiving is another team's call).
EXCLUDED = {"epr-prn-rep-exp-facade", "epr-prn-rep-exp-frontend"}

rows = []


def row(status, area, resource, needed_by, detail, action=""):
    """action: what to ask for (MISSING) or do (WARN)."""
    rows.append((status, area, resource, needed_by, detail, action))
    print(f"{status:8} {area:10} {resource:32} {detail} [{needed_by}]")
    if status == "MISSING":
        print(f"##vso[task.logissue type=error]{area} {resource}: {detail} (needed by {needed_by})")
    elif status == "WARN":
        print(f"##vso[task.logissue type=warning]{area} {resource}: {detail} ({needed_by})")


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
    return ", ".join(names) if len(names) <= limit else f"{len(names)} repos"


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
resources = [l.split() for l in open(RESOURCES) if l.strip() and l.split()[0] not in EXCLUDED]

print(f"Environment {ENV}, connection {CONNECTION}\n")

# Library
if LIBRARY_VALUE == EXPECTED:
    row("OK", "Library", f"ServiceConnection{ENV}", "all repos", f"= {LIBRARY_VALUE}")
else:
    row("WARN", "Library", f"ServiceConnection{ENV}", "all repos",
        f"= {LIBRARY_VALUE}, not {EXPECTED}", f"switch the library to {EXPECTED} once nothing is missing")
if CONNECTION != LIBRARY_VALUE:
    row("INFO", "Library", CONNECTION, "-", "tested directly (appConnection), not the library value")

# Repo vars that still name a connection instead of taking it from the library
for repo, file, key, value in resources:
    if key in ("azureSubscription", "acr.Subscription") and not value.startswith("$(ServiceConnection"):
        row("WARN", "Repo vars", f"{key} = {value}", repo, f"{file} does not use the library",
            f"change to $(ServiceConnection{ENV})")

# Apps
rc, sites, err = az("resource", "list", "--resource-type", "Microsoft.Web/sites", "--query", "[].{name:name, rg:resourceGroup, id:id}")
visible = {s["name"].lower(): s for s in sites or []}
by_rg = collections.defaultdict(list)
for repo, file, key, app in resources:
    if not key.lower().endswith("servicename"):
        continue
    site = visible.get(app.lower())
    if site:
        by_rg[site["rg"].upper()].append((repo, site))
    else:
        row("MISSING", "App", app, repo, "app not visible - deploy and restart fail",
            "read on the app's resource group (e.g. DEFRA Custom Role - App Deploy)")
for rg, apps in sorted(by_rg.items()):
    repos = [r for r, _ in apps]
    perms = permissions(apps[0][1]["id"])
    if perms is None:
        row("WARN", "App", f"{rg} ({len(apps)} apps)", join(repos), "cannot read the connection's role assignments here",
            "check the roles by hand")
        continue
    lacking = [a for a in APP_ACTIONS if not allowed(perms, a)]
    if lacking:
        row("MISSING", "App", f"{rg} ({len(apps)} apps)", join(repos),
            f"roles {join(perms[4])} do not allow deploy/restart", f"{', '.join(lacking)} on {rg}")
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
    # Reported with the pull check below when that is missing too (AcrPull fixes both).
    login_problem = None
    if login.returncode != 0:
        login_problem = f"az acr login failed: {short(login.stderr)}"
    elif "admin" in text.lower() or "401" in text:
        login_problem = "Azure AD login refused (401) - only works through the admin user"
    else:
        row("OK", "Registry", acr, join(repos), "Azure AD login")
    rc, reg, err = az("acr", "show", "--name", acr, "--query", "{id:id}")
    if rc != 0 or reg is None:
        if login_problem:
            row("MISSING", "Registry", acr, join(repos), login_problem, f"AcrPull on {acr}")
        row("MISSING", "Registry", acr, join(repos), f"cannot read the registry: {short(err)}", f"Microsoft.ContainerRegistry/registries/read on {acr}")
        continue
    perms = permissions(reg["id"])
    if perms is None:
        if login_problem:
            row("MISSING", "Registry", acr, join(repos), login_problem, f"AcrPull on {acr}")
        row("WARN", "Registry", acr, join(repos), "cannot read the connection's role assignments here", "check the roles by hand")
        continue
    # what: (action, data action, what to ask for)
    needs = {"pull (SQL migration)": ("Microsoft.ContainerRegistry/registries/pull/read",
                                      "Microsoft.ContainerRegistry/registries/repositories/content/read", "AcrPull")}
    if not ENV.startswith("DEV"):
        needs["import (Copy Image)"] = ("Microsoft.ContainerRegistry/registries/importImage/action", None,
                                        "Microsoft.ContainerRegistry/registries/importImage/action")
    if ENV == "DEV4":
        needs["push (CI)"] = ("Microsoft.ContainerRegistry/registries/push/write",
                              "Microsoft.ContainerRegistry/registries/repositories/content/write", "AcrPush")
    for what, (action, data_action, ask) in needs.items():
        if allowed(perms, action) or (data_action and allowed(perms, data_action, data=True)):
            row("OK", "Registry", acr, join(repos), f"{what} via {join(perms[4])}")
        else:
            problem = f"{what} not allowed"
            if login_problem and ask == "AcrPull":
                problem, login_problem = f"{problem}; {login_problem}", None
            row("MISSING", "Registry", acr, join(repos), problem, f"{ask} on {acr}")
    if login_problem:
        row("MISSING", "Registry", acr, join(repos), login_problem, f"AcrPull on {acr}")

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
        row("WARN", "Key Vault", kv, join(repos), "RWDSql-AdminLogin does not exist in this vault", "check the vault name in the repos' vars")
    elif "Forbidden" in err or "not authorized" in err.lower() or "does not have" in err.lower():
        row("MISSING", "Key Vault", kv, join(repos), "cannot read secrets (SQL migration fails)",
            f"Key Vault Secrets User (or a get secret access policy) on {kv}")
    else:
        row("WARN", "Key Vault", kv, join(repos), f"could not check: {short(err)}", "check by hand")

# Storage (shutter page, B2C styling): uploads use --auth-mode login
if ENV in STORAGE:
    account, containers = STORAGE[ENV]
    for container, repo in containers.items():
        rc, _, err = az("storage", "blob", "list", "--container-name", container, "--account-name", account,
                        "--auth-mode", "login", "--num-results", "1", "--query", "[].name")
        if rc == 0:
            row("OK", "Storage", f"{account}/{container}", repo, "Azure AD access")
        else:
            row("MISSING", "Storage", f"{account}/{container}", repo, f"no Azure AD access - upload fails: {short(err)}", f"Storage Blob Data Contributor on {account}")

# Summary
missing = sum(r[0] == "MISSING" for r in rows)
warn = sum(r[0] == "WARN" for r in rows)
ok = sum(r[0] == "OK" for r in rows)
headline = f"{ENV} {CONNECTION}: {missing} missing, {warn} warnings, {ok} OK"
order = {"MISSING": 0, "WARN": 1, "INFO": 2, "OK": 3}
rows.sort(key=lambda r: (order[r[0]], r[1], r[2].lower()))

# Table at the end of the log
ICON = {"MISSING": "✗ MISSING", "WARN": "! WARN", "INFO": "i INFO", "OK": "✓ OK"}
cols = [("Result", 9), ("Area", 9), ("Resource", 30), ("Needed by", 34), ("Problem / status", 38), ("Ask for / action", 38)]
line = "+" + "+".join("-" * (w + 2) for _, w in cols) + "+"
print("\n" + line)
print("| " + " | ".join(f"{h:{w}}" for h, w in cols) + " |")
print(line.replace("-", "="))
for status, area, resource, needed_by, detail, action in rows:
    cells = [textwrap.wrap(str(v), w) or [""] for v, (_, w) in
             zip((ICON[status], area, resource, needed_by, detail, action), cols)]
    for n in range(max(map(len, cells))):
        print("| " + " | ".join(f"{(c[n] if n < len(c) else ''):{w}}" for c, (_, w) in zip(cells, cols)) + " |")
    print(line)
print(f"\n##[section]{headline}")
print(f"##vso[build.updatebuildnumber]{os.environ.get('BUILD_BUILDID', '')} {ENV} {missing} missing {warn} warnings")

# Markdown on the run's summary page
cell = lambda v: str(v).replace("|", "\\|")
sections = [
    ("MISSING", "❌ Missing - ask the service connection team", True),
    ("WARN", "⚠️ Warnings - for us to fix or check", True),
    ("INFO", "ℹ️ Info", False),
    ("OK", "✅ OK", False),
]
with open(SUMMARY, "w") as f:
    f.write(f"## {ENV} - {CONNECTION}\n\n**{missing}** missing · **{warn}** warnings · **{ok}** OK\n")
    for status, title, with_action in sections:
        these = [r for r in rows if r[0] == status]
        if not these:
            continue
        f.write(f"\n### {title}\n\n| Area | Resource | Needed by | {'Problem' if with_action else 'Status'} |"
                + (" Ask for / action |" if with_action else "") + "\n|---|---|---|---|" + ("---|" if with_action else "") + "\n")
        for _, area, resource, needed_by, detail, action in these:
            f.write(f"| {area} | {cell(resource)} | {cell(needed_by)} | {cell(detail)} |"
                    + (f" {cell(action)} |" if with_action else "") + "\n")
print(f"##vso[task.uploadsummary]{SUMMARY}")
sys.exit(1 if missing else 0)
