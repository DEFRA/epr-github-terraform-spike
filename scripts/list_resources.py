"""Write pipelines/resources/<ENV>.txt for pipelines/service-connection-check.yaml: per environment,
each EPR repo's web/function apps, registry and repository, Key Vault and service connection variables, read from
the vars files on each repo's origin/main (fetched first; working trees are not used).

usage: python3 scripts/list_resources.py <clones-dir> [<repo> ...]
With no repos, every epr-* clone in <clones-dir> that has vars files on main is included.
Lines: "<repo> <vars file> <variable> <value>". Re-run when apps, repos or environments change.
"""
import os
import re
import subprocess
import sys

import yaml

ENVS = ["DEV1", "DEV2", "DEV3", "DEV4", "DEV7", "DEV8", "DEV9", "DEV14", "DEV15", "TST1", "PRE1", "PRE2"]
VARS_DIRS = ["pipelines/vars", "vars", "pipelines/vars-regulator"]
KEYS = r"servicename$|^acr\.azureContainerRegistryName$|^acr\.repositoryName$|^kvName$|^azureSubscription$|^acr\.Subscription$"
OUT = os.path.join(os.path.dirname(__file__), "..", "pipelines", "resources")
CONFIG_REPO = "epr-app-config-settings"


def candidates(env):
    if env == "TST1":
        return ["tst.yaml"]
    if env == "PRE1":
        return ["preprod.yaml"]
    if env == "PRE2":
        return ["preprod2.yaml"]
    return [f"{env}-development.yaml", f"{env}.yaml"]


def load(text):
    v = (yaml.safe_load(text) or {}).get("variables")
    if isinstance(v, dict):
        return {k: str(x) for k, x in v.items()}
    return {i["name"]: str(i.get("value")) for i in v or [] if isinstance(i, dict) and "name" in i}


def config_lines(clones, env):
    """The apps pipeline_single_configuration (epr-app-config-settings) can update in env, from its
    settings files: DEV uses dev/ with X replaced by the environment's spoke, others <env>/."""
    path = os.path.join(clones, CONFIG_REPO)
    if not os.path.isdir(os.path.join(path, ".git")):
        return []
    git = lambda *a: subprocess.run(["git", "-C", path, *a], capture_output=True, text=True).stdout
    vars_file = f"pipelines/vars/{env.lower()}.yaml"
    # Read directly: some of these files have tabs that are not valid YAML for the parser here.
    spoke = re.search(r"^\s*environmentSpoke:\s*(\S+)", git("show", f"origin/main:{vars_file}"), re.M)[1]
    lines = [f"{CONFIG_REPO} {vars_file} configResourceGroup {env[:3]}RWDWEBRG{spoke}401"]
    folder = "dev" if env.startswith("DEV") else env.lower()
    for name in git("ls-tree", "--name-only", f"origin/main:{folder}").split():
        if folder == "dev":
            match = re.fullmatch(r"([A-Z]+)X(\d+)\.json", name)
            if not match:
                continue
            name = f"{match[1]}{spoke}{match[2]}.json"
        if name.endswith(".json"):
            lines.append(f"{CONFIG_REPO} {folder}/ configApp {name[:-5]}")
    return lines


def main():
    clones = sys.argv[1]
    repos = sys.argv[2:] or sorted(d for d in os.listdir(clones)
                                   if d.startswith("epr-") and os.path.isdir(os.path.join(clones, d, ".git")))
    files = {}
    for repo in repos:
        git = lambda *a: subprocess.run(["git", "-C", os.path.join(clones, repo), *a],
                                        capture_output=True, text=True).stdout
        git("fetch", "-q")
        for path in git("ls-tree", "-r", "--name-only", "origin/main", "--", *VARS_DIRS).split():
            files[(repo, path)] = lambda r=repo, p=path: subprocess.run(
                ["git", "-C", os.path.join(clones, r), "show", f"origin/main:{p}"],
                capture_output=True, text=True).stdout
    used = set()
    for env in ENVS:
        lines = []
        for repo in repos:
            for d in VARS_DIRS:
                for name in candidates(env):
                    path = f"{d}/{name}"
                    if (repo, path) in files:
                        used.add(repo)
                        for key, value in load(files[(repo, path)]()).items():
                            if re.search(KEYS, key, re.I):
                                lines.append(f"{repo} {path} {key} {value}")
                        break
        lines += config_lines(clones, env)
        with open(os.path.join(OUT, f"{env}.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
    print(f"{len(used)} repos with vars files: {', '.join(sorted(used))}")


if __name__ == "__main__":
    main()
