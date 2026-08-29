"""vitals.py -- stage 4. Runs Lighthouse against a served site and extracts lab metrics.

IMPORTANT -- these are LAB metrics from a local run, not field data. They are tagged
MEASURED-lab and must never be reported as a site's real Core Web Vitals: production
has a CDN, real network conditions, and real devices. Field CWV comes from CrUX via
Search Console, which is a HANDOFF item (see references/capability-tiers.md).

On Windows, chrome-launcher frequently fails to delete its temp profile after the run
and exits non-zero (EPERM) even though the report was written correctly. Success is
therefore determined by parsing the report, never by the exit code.

Usage:
  python vitals.py --base http://localhost:8765 --pages / /about/ --out vitals.json
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

METRICS = {
    "largest-contentful-paint": "LCP",
    "cumulative-layout-shift": "CLS",
    "total-blocking-time": "TBT",
    "first-contentful-paint": "FCP",
    "speed-index": "SI",
    "interactive": "TTI",
}

PROFILES = {
    "mobile": [],                                   # lighthouse default is mobile
    "desktop": ["--preset=desktop"],
}


def run_one(url, profile, workdir, timeout=180):
    """Run Lighthouse once. Returns (metrics_dict, error_or_None)."""
    out_path = os.path.join(workdir, "lh-{0}.json".format(abs(hash((url, profile)))))
    cmd = [
        "npx", "--no-install", "lighthouse", url,
        "--output=json", "--output-path=" + out_path,
        "--only-categories=performance",
        "--chrome-flags=--headless=new --no-sandbox --disable-gpu",
        "--quiet",
    ] + PROFILES[profile]

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       shell=(os.name == "nt"))
    except subprocess.TimeoutExpired:
        return None, "lighthouse timed out after {0}s".format(timeout)
    except FileNotFoundError:
        return None, "npx/lighthouse not found on PATH"

    # Exit code is unreliable on Windows (temp-profile cleanup EPERM). Trust the report.
    if not os.path.isfile(out_path):
        return None, "lighthouse produced no report"
    try:
        with open(out_path, encoding="utf-8") as f:
            rep = json.load(f)
    except Exception as e:
        return None, "unparseable report: {0}".format(e)

    if rep.get("runtimeError"):
        return None, "runtimeError: {0}".format(rep["runtimeError"].get("message", "?"))

    audits = rep.get("audits", {})
    metrics = {k: audits.get(k, {}).get("numericValue") for k in METRICS if k in audits}
    metrics["_score"] = (rep.get("categories", {}).get("performance", {}) or {}).get("score")
    metrics["_total_bytes"] = audits.get("total-byte-weight", {}).get("numericValue")
    opps = []
    for key, a in audits.items():
        if a.get("details", {}).get("type") == "opportunity":
            saving = a.get("details", {}).get("overallSavingsMs") or 0
            if saving > 100:
                opps.append({"id": key, "title": a.get("title"), "savings_ms": round(saving)})
    opps.sort(key=lambda o: -o["savings_ms"])
    return {"metrics": metrics, "opportunities": opps[:8],
            "lighthouse_version": rep.get("lighthouseVersion")}, None


def normalize_page(path):
    """Guard against MSYS/Git-Bash path conversion, which silently rewrites a bare
    '/' argument into a Windows path like 'C:/Program Files/Git/'. Left unchecked
    that produces a nonsense URL and a confusing 'no report' error."""
    if not path:
        return "/"
    if ":" in path[:3] or path.lower().startswith("c:/program files"):
        raise SystemExit(
            "ERROR: page argument '{0}' was rewritten by the shell's path conversion.\n"
            "       Re-run with MSYS_NO_PATHCONV=1, or pass paths via --pages-json.".format(path))
    return path if path.startswith("/") else "/" + path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="origin of a RUNNING server, e.g. http://localhost:8765")
    ap.add_argument("--pages", nargs="*", default=["/"], help="paths to measure")
    ap.add_argument("--profiles", nargs="*", default=["mobile", "desktop"])
    ap.add_argument("--pages-json",
                    help="JSON list of paths -- immune to shell path mangling")
    ap.add_argument("--out", default="vitals.json")
    args = ap.parse_args()

    pages = json.loads(args.pages_json) if args.pages_json else args.pages
    pages = [normalize_page(p) for p in pages]

    workdir = tempfile.mkdtemp(prefix="seo-repair-lh-")
    runs, errors = [], []
    try:
        for path in pages:
            url = args.base.rstrip("/") + path
            for profile in args.profiles:
                print("measuring {0} [{1}] ...".format(url, profile))
                res, err = run_one(url, profile, workdir)
                if err:
                    errors.append({"page": path, "profile": profile, "error": err})
                    print("  FAILED: " + err)
                    continue
                m = res["metrics"]
                runs.append({"page": path, "profile": profile,
                             "metrics": m, "opportunities": res["opportunities"]})
                print("  LCP {0} CLS {1} TBT {2} score {3}".format(
                    _f(m.get("largest-contentful-paint")),
                    round(m.get("cumulative-layout-shift") or 0, 3),
                    _f(m.get("total-blocking-time")),
                    m.get("_score")))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({
            "tier": "MEASURED-lab",
            "caveat": "Lab metrics from a local Lighthouse run. Not field data. "
                      "Real-user Core Web Vitals must be read from Search Console (CrUX).",
            "base": args.base,
            "runs": runs,
            "errors": errors,
        }, f, ensure_ascii=False, indent=1)
    print("wrote {0} ({1} runs, {2} errors)".format(args.out, len(runs), len(errors)))
    return 0 if runs else 1


def _f(v):
    return "-" if v is None else "{0:.0f}ms".format(v)


if __name__ == "__main__":
    sys.exit(main())
