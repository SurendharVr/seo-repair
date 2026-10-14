"""seo_repair.py -- one command for the whole audit pipeline, no agent required.

  python seo_repair.py audit  <folder | https://site> [--out DIR] [--fail-on high]
  python seo_repair.py verify <folder | https://site> --baseline DIR/findings.json [--out DIR]

`audit` chains crawl -> schema_check -> audit -> report -> dashboard and leaves
everything in --out (default ./seo-repair-out): findings.json, SEO-REPORT-<date>.md and
dashboard.html. It edits nothing in the audited site. `verify` re-audits and diffs
against an earlier findings.json. Applying fixes stays a Claude Code job, because each
edit needs your approval.

--fail-on makes the exit code non-zero when a finding at that severity or worse is open,
so it can gate CI.
"""
import argparse
import json
import os
import subprocess
import sys
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
SEVERITY = ["low", "medium", "high", "critical"]


def run(script, *args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, os.path.join(HERE, script)] + [str(a) for a in args],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    if r.returncode:
        sys.exit("{0} failed:\n{1}{2}".format(script, r.stdout, r.stderr))
    return r.stdout


def audit(target, out, max_pages, site_name):
    os.makedirs(out, exist_ok=True)
    p = lambda name: os.path.join(out, name)
    if target.startswith(("http://", "https://")):
        u = urlsplit(target)
        crawl_args = ["--mode", "http", "--start", target,
                      "--base-url", "{0}://{1}".format(u.scheme, u.netloc),
                      "--max-pages", max_pages]
    elif os.path.isdir(target):
        crawl_args = ["--root", target]
    else:
        sys.exit("not a folder or an http(s) URL: " + target)

    run("crawl.py", *crawl_args, "--out", p("crawl.json"))
    run("schema_check.py", "--crawl", p("crawl.json"), "--out", p("schema.json"))
    summary = run("audit.py", "--crawl", p("crawl.json"), "--schema", p("schema.json"),
                  "--out", p("findings.json"))
    report_args = ["--crawl", p("crawl.json"), "--findings", p("findings.json"),
                   "--outdir", out, "--data-out", p("report-data.json")]
    if site_name:
        report_args += ["--site-name", site_name]
    run("report.py", *report_args)
    run("build_dashboard.py", "--data", p("report-data.json"), "--out", p("dashboard.html"))
    print(summary.rstrip())
    print("\nreport:    " + [f for f in sorted(os.listdir(out)) if f.startswith("SEO-REPORT")][-1])
    print("dashboard: dashboard.html   (in " + os.path.abspath(out) + ")")
    return p("findings.json")


def worst_open(findings_path):
    sev = {f["severity"] for f in json.load(open(findings_path, encoding="utf-8"))["findings"]}
    return max((SEVERITY.index(s) for s in sev if s in SEVERITY), default=-1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("audit", "verify"):
        sp = sub.add_parser(name)
        sp.add_argument("target", help="site folder or http(s) URL")
        sp.add_argument("--out", default="seo-repair-out")
        sp.add_argument("--max-pages", type=int, default=200)
        sp.add_argument("--site-name")
        sp.add_argument("--fail-on", choices=SEVERITY[1:])
    sub.choices["verify"].add_argument("--baseline", required=True,
                                       help="findings.json from an earlier audit")
    args = ap.parse_args()

    findings = audit(args.target, args.out, args.max_pages, args.site_name)
    if args.cmd == "verify":
        print(run("verify.py", "--baseline", args.baseline, "--current", findings,
                  "--out", os.path.join(args.out, "verify.json")).rstrip())
    if args.fail_on and worst_open(findings) >= SEVERITY.index(args.fail_on):
        sys.exit("FAIL: open finding at '{0}' or worse".format(args.fail_on))


if __name__ == "__main__":
    main()
