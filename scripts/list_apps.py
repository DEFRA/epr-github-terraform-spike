"""Write pipelines/apps/<ENV>.txt: the web/function apps each EPR repo deploys to per
environment, read from the repos' vars files (run from the folder holding the clones).

usage: python3 scripts/list_apps.py <clones-dir> <repo> [<repo> ...]
Lines: "<repo> <vars file> <variable> <app name>". Repos with no vars file for an
environment are listed in pipelines/apps/MISSING.txt.
"""
import os, re, sys, yaml

ENVS = ["DEV1", "DEV2", "DEV3", "DEV4", "DEV7", "DEV8", "DEV9", "DEV14", "DEV15", "TST1"]
VARS_DIRS = ["pipelines/vars", "vars", "pipelines/vars-regulator"]
OUT = os.path.join(os.path.dirname(__file__), "..", "pipelines", "apps")


def candidates(env):
    if env == "TST1":
        return ["tst.yaml"]
    return [f"{env}-development.yaml", f"{env}.yaml"]


def load(path):
    v = (yaml.safe_load(open(path)) or {}).get("variables")
    if isinstance(v, dict):
        return {k: str(x) for k, x in v.items()}
    return {i["name"]: str(i.get("value")) for i in v or [] if "name" in i}


def main():
    clones, repos = sys.argv[1], sys.argv[2:]
    missing = []
    for env in ENVS:
        lines = []
        for repo in repos:
            found = False
            for d in VARS_DIRS:
                for name in candidates(env):
                    path = os.path.join(clones, repo, d, name)
                    if os.path.exists(path):
                        found = True
                        for key, value in load(path).items():
                            if re.search(r"servicename$", key, re.I):
                                lines.append(f"{repo} {d}/{name} {key} {value}")
                        break
            if not found:
                missing.append(f"{env} {repo}")
        with open(os.path.join(OUT, f"{env}.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
    with open(os.path.join(OUT, "MISSING.txt"), "w") as f:
        f.write("\n".join(missing) + ("\n" if missing else ""))


if __name__ == "__main__":
    main()
