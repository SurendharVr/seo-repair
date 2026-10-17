"""replay.py -- Task 5 of the evidence plan: replay real commits through the frozen gate.

For each repository in repos.json, build its last N first-parent commits before the cutoff,
oldest first, audit each build, and run the regression gate on every consecutive pair:
each commit against its first parent. One build is the head of one comparison and the
base of the next, so N comparisons cost N+1 builds.

  python bench/replay/replay.py                     # every repository, N from repos.json
  python bench/replay/replay.py --pilot             # the first 2 repositories, 20 commits each
  python bench/replay/replay.py --only vuejs/docs
  python bench/replay/replay.py --pilot --list      # the selection as JSON, for a CI matrix

Writes bench/replay/results.csv, one row per comparison. Each commit's build and audit are
cached under --out by SHA, so a re-run only does what is missing. A failed build is never a
pass or a fail: both comparisons that touch it are recorded as skipped. Stopping a run
mid-build records that commit as build_failed too; delete its folder under --out before
re-running.

sitemend runs from the installed package, which must be the version frozen in
bench/PREREGISTRATION.md. --scripts runs another copy instead (the tests use the source tree).

This runs each repository's own build command, which runs that repository's code.
"""
import argparse
import csv
import json
import os
import shutil
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = "1.6.0"
PILOT = (2, 20)
FIELDS = ["repo", "base_sha", "head_sha", "build_seconds", "audit_seconds", "gate_result",
          "new_findings_json_path"]


def git(cwd, *args):
    return subprocess.run(["git"] + list(args), cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def installed_scripts():
    """The installed sitemend's folder, refusing any version but the frozen one: a replay
    measured on a moving version measures nothing."""
    from importlib import metadata, util
    try:
        version = metadata.version("sitemend")
    except metadata.PackageNotFoundError:
        version = None
    if version != FROZEN:
        sys.exit("the replay is frozen on sitemend {0}; this Python has {1}. "
                 "pip install sitemend=={0}".format(FROZEN, version or "none"))
    return list(util.find_spec("sitemend").submodule_search_locations)[0]


def clone(entry, work):
    """Clone once. The cutoff is in the past, so there is never anything to fetch."""
    path = os.path.join(work, entry["repo"].replace("/", "__"))
    if not os.path.isdir(path):
        url = entry.get("url") or "https://github.com/{0}.git".format(entry["repo"])
        # Blobs are fetched on checkout, so only the commits replayed are downloaded.
        git(work, "clone", "--quiet", "--filter=blob:none", url, path)
    return path


def commits(path, cutoff, n):
    """The last n+1 first-parent commits on the default branch before cutoff (committer
    date), oldest first: n comparisons of a commit with its first parent."""
    out = git(path, "rev-list", "--first-parent", "--before=" + cutoff, "-n", str(n + 1),
              "origin/HEAD")
    return out.split()[::-1]


def build_and_audit(entry, path, sha, outdir, py, scripts, env, timeout):
    """Build one commit and audit its output, recorded in <outdir>/build.json. A commit
    that already has one is not built again."""
    record = os.path.join(outdir, "build.json")
    if os.path.exists(record):
        with open(record, encoding="utf-8") as fh:
            return json.load(fh)
    os.makedirs(outdir, exist_ok=True)
    git(path, "checkout", "--quiet", "--force", "--detach", sha)
    # Keep installed dependencies between commits, as a CI cache would; remove every other
    # untracked file, above all the previous commit's build output.
    git(path, "clean", "-ffdxq", "-e", "node_modules")
    rec = {"sha": sha, "status": "ok", "build_seconds": None, "audit_seconds": None,
           "node": env.get("REPLAY_NODE")}
    start = time.monotonic()
    with open(os.path.join(outdir, "build.log"), "w", encoding="utf-8", errors="replace") as log:
        try:
            ok = subprocess.run(entry["build_command"], shell=True, cwd=path, env=env, stdout=log,
                                stderr=subprocess.STDOUT, timeout=timeout).returncode == 0
        except subprocess.TimeoutExpired:
            # ponytail: on Windows a shell's children can outlive the timeout; the next
            # checkout still works, but kill stray node processes by hand if it happens.
            log.write("\n[replay] build timed out after {0} s\n".format(timeout))
            ok = False
    rec["build_seconds"] = round(time.monotonic() - start, 1)
    site = os.path.join(path, entry["site_dir"])
    if not ok or not os.path.isdir(site):
        rec["status"] = "build_failed"
    else:
        start = time.monotonic()
        r = subprocess.run([py, os.path.join(scripts, "sitemend.py"), "audit", site, "--out", outdir],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        rec["audit_seconds"] = round(time.monotonic() - start, 1)
        with open(os.path.join(outdir, "audit.log"), "w", encoding="utf-8") as log:
            log.write(r.stdout + r.stderr)
        if r.returncode != 0:
            rec["status"] = "audit_failed"
    with open(record, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    return rec


def compare(py, scripts, base_dir, head_dir, env):
    """The gate, head against base: pass or fail, from verify.py's exit code."""
    out = os.path.join(head_dir, "verify.json")
    r = subprocess.run([py, os.path.join(scripts, "verify.py"),
                        "--baseline", os.path.join(base_dir, "findings.json"),
                        "--current", os.path.join(head_dir, "findings.json"), "--out", out],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return {0: "pass", 1: "fail"}.get(r.returncode, "error"), out


def tool_env(work):
    """Package managers come from corepack, at the version each repository's packageManager
    field names, through shims in the work folder so nothing outside it changes."""
    env = dict(os.environ, CI="true", COREPACK_ENABLE_DOWNLOAD_PROMPT="0", PYTHONIOENCODING="utf-8")
    if shutil.which("corepack"):
        shims = os.path.join(work, "shims")
        if not os.path.isdir(shims):
            os.makedirs(shims)
            subprocess.run(["corepack", "enable", "--install-directory", shims], check=True,
                           capture_output=True, shell=os.name == "nt")
        env["PATH"] = shims + os.pathsep + env.get("PATH", "")
    node = shutil.which("node", path=env["PATH"])
    if node:
        env["REPLAY_NODE"] = subprocess.run([node, "--version"], capture_output=True,
                                            text=True).stdout.strip()
    return env


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default=os.path.join(HERE, "repos.json"))
    ap.add_argument("--work", default=os.path.join(HERE, "work"), help="clones go here")
    ap.add_argument("--out", default=os.path.join(HERE, "out"), help="per-commit audits go here")
    ap.add_argument("--results", default=os.path.join(HERE, "results.csv"))
    ap.add_argument("--pilot", action="store_true",
                    help="the first {0} repositories, {1} commits each".format(*PILOT))
    ap.add_argument("--only", help="comma-separated owner/name list")
    ap.add_argument("--scripts", help="sitemend's scripts folder (default: the installed package)")
    ap.add_argument("--timeout", type=int, default=1800, help="seconds per build")
    ap.add_argument("--list", action="store_true",
                    help="print the selected repositories as JSON and exit")
    args = ap.parse_args(argv)

    with open(args.config, encoding="utf-8") as fh:
        repos = json.load(fh)
    if args.only:
        wanted = set(args.only.split(","))
        repos = [e for e in repos if e["repo"] in wanted]
    if args.pilot and not args.only:
        repos = repos[:PILOT[0]]
    if args.list:
        print(json.dumps([{"repo": e["repo"], "slug": e["repo"].replace("/", "__"),
                           "node_version": str(e.get("node_version", ""))} for e in repos]))
        return 0
    scripts = args.scripts or installed_scripts()
    os.makedirs(args.work, exist_ok=True)
    env = tool_env(args.work)

    rows, builds = [], []
    for entry in repos:
        n = PILOT[1] if args.pilot else entry["commits"]
        want = str(entry.get("node_version", ""))
        if want and not (env.get("REPLAY_NODE") or "").lstrip("v").startswith(want.split(".")[0] + "."):
            print("warning: {0} wants Node {1}; this run has {2}".format(
                entry["repo"], want, env.get("REPLAY_NODE")), file=sys.stderr)
        path = clone(entry, args.work)
        shas = commits(path, entry["ref_cutoff"], n)
        recs = []
        for i, sha in enumerate(shas, 1):
            outdir = os.path.join(args.out, entry["repo"].replace("/", "__"), sha)
            rec = build_and_audit(entry, path, sha, outdir, sys.executable, scripts, env, args.timeout)
            print("{0} {1}/{2} {3} {4} build {5} s audit {6} s".format(
                entry["repo"], i, len(shas), sha[:10], rec["status"], rec["build_seconds"],
                "-" if rec["audit_seconds"] is None else rec["audit_seconds"]), flush=True)
            recs.append((rec, outdir))
        builds.extend(r for r, _ in recs)
        for (base, base_dir), (head, head_dir) in zip(recs, recs[1:]):
            row = {"repo": entry["repo"], "base_sha": base["sha"], "head_sha": head["sha"],
                   "build_seconds": head["build_seconds"], "audit_seconds": head["audit_seconds"]}
            if base["status"] == head["status"] == "ok":
                gate, out = compare(sys.executable, scripts, base_dir, head_dir, env)
                row["gate_result"] = gate
                row["new_findings_json_path"] = os.path.relpath(
                    out, os.path.dirname(os.path.abspath(args.results))).replace(os.sep, "/")
            else:
                failed = head if head["status"] != "ok" else base
                row["gate_result"] = "skipped_" + failed["status"]
                row["new_findings_json_path"] = ""
            rows.append(row)

    with open(args.results, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    ok = [b for b in builds if b["status"] == "ok"]
    print("\n{0} builds ({1} ok, {2} failed), {3} comparisons -> {4}".format(
        len(builds), len(ok), len(builds) - len(ok), len(rows), args.results))
    if ok:
        print("median build {0:.1f} s, median audit {1:.1f} s per commit".format(
            statistics.median(b["build_seconds"] for b in ok),
            statistics.median(b["audit_seconds"] for b in ok)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
