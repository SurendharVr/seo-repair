# Pre-registration: replay benchmark

Written before any replay data exists. Every value below is fixed before the harness
(Task 5) runs on real repositories, and the decision rules in section 5 are applied to the
results **exactly as written**. Changing any value after data exists invalidates the run.

Fields marked `____` are for B to fill in. Where the plan proposed a value, it is shown
next to the blank for reference only; the blank is the value that counts.

**Status:** DRAFT, not signed off. The replay must not run on real data until this file
is filled in, signed below, and merged.

## 1. Frozen version

- Package: `sitemend==1.6.0`
- Tag: `v1.6.0`
- Commit: `badbb38e8c8235f35e7b8ffcdc12f9785263cb13`

No rule, threshold or severity changes until the report (Task 8) is published. Rule bugs
found during the run go into `bench/ledger.csv` and an issue, and are not fixed.

## 2. Repository list

Criteria, all required:

- Built with a supported stack: Next.js static export, Astro/Starlight, Docusaurus,
  VitePress, or Hugo.
- Builds with one command on a clean Ubuntu runner.
- At least 75 commits to the default branch in the 12 months before the cutoff date.
- Not used to tune rules. That excludes the 20 sites in `docs/benchmark-2026-10.md`,
  which names none of them, so B holds that list, plus the three sites the 1.5.2
  crawler fixes came from: biomejs.dev, pnpm.io and turborepo.dev.

Target size: 10 to 15 repositories.

| # | Repository (owner/name) | Stack | Build command | Site dir | Node version |
|---|---|---|---|---|---|
| 1 | ____ | ____ | ____ | ____ | ____ |
| 2 | ____ | ____ | ____ | ____ | ____ |
| 3 | ____ | ____ | ____ | ____ | ____ |
| 4 | ____ | ____ | ____ | ____ | ____ |
| 5 | ____ | ____ | ____ | ____ | ____ |
| 6 | ____ | ____ | ____ | ____ | ____ |
| 7 | ____ | ____ | ____ | ____ | ____ |
| 8 | ____ | ____ | ____ | ____ | ____ |
| 9 | ____ | ____ | ____ | ____ | ____ |
| 10 | ____ | ____ | ____ | ____ | ____ |
| 11 | ____ | ____ | ____ | ____ | ____ |
| 12 | ____ | ____ | ____ | ____ | ____ |
| 13 | ____ | ____ | ____ | ____ | ____ |
| 14 | ____ | ____ | ____ | ____ | ____ |
| 15 | ____ | ____ | ____ | ____ | ____ |

Excluded tuning sites checked against this list by: ____

## 3. Commit range

The last N first-parent commits on each repository's default branch before the cutoff.

- N (commits per repository): ____
- Cutoff date (UTC): ____

A commit whose build fails is recorded as `build_failed`, and both comparisons that touch
it are skipped. Skipped comparisons count toward neither frequency nor precision.

## 4. Pass/fail thresholds

| Measure | Unit | Proposed in the plan | Accepted value |
|---|---|---|---|
| Blocking precision | per blocking finding: `true_regression` / all blocking findings with a verdict | at least 95% (at most 1 false block in 20) | ____ |
| Frequency | per commit: commits introducing at least one verified critical or high regression / commits compared | at least 1 per 100 commits | ____ |
| Seeded recall (Task 7) | per mutation, critical and high mutation types | at least 90% | ____ |

Findings with the verdict `unclear` are counted as: ____

## 5. Decision rules

Applied to the full run's results as written, with the outcome at the top of the report.

- All three thresholds met: continue the release-gate positioning (Phase 4).
- Precision fails: stop all outreach and fix rules under the stability policy in
  `RELEASING.md`. Every confirmed false positive becomes a permanent test.
- Precision passes but frequency fails: re-test as a pre-launch and redesign audit, not a
  per-PR gate.
- Recall fails: do not market it as a gate until the missed mutation types are fixed.

## 6. Known limitations

- Replay builds each commit locally. It cannot reproduce environment variables, hosting
  configuration, or runtime behaviour.
- So preview-host leaks and soft 404s will be **under-counted**. A low replay count for
  those two types is not evidence that they are rare. The prospective run (Task 9) is the
  evidence for them.
- Seeded mutations (Task 7) change the built output, not the source, so they do not test
  whether a framework would produce that output.

## Sign-off

- Filled in and approved by (B): ____
- Date: ____
- Merged before any replay run on real data (commit SHA of the merge): ____
