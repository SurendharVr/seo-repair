# Releasing

Every step here exists because something actually went wrong without it. None of it is
ceremony.

## Before tagging

**1. Tests pass locally.**

```bash
python tests/test_skill.py
```

A failure means *"was this behaviour meant to change"*, not *"make it pass"*. Most of
these tests pin down a calibration decision made after a real false positive.

**2. The demo site still reports what it claims to.**

```bash
cd scripts
python crawl.py        --root ../examples/demo-site --out /tmp/crawl.json
python schema_check.py --crawl /tmp/crawl.json      --out /tmp/schema.json
python audit.py        --crawl /tmp/crawl.json --schema /tmp/schema.json --out /tmp/findings.json
```

Compare against [`examples/demo-site/EXPECTED.md`](examples/demo-site/EXPECTED.md).

**This is a coupling worth knowing about.** If you add, remove or retune a rule, three
things must move together or CI fails and the docs start lying:

- the finding counts in `EXPECTED.md`
- the defect table in `EXPECTED.md`
- the `required` set of finding ids in `.github/workflows/tests.yml`

`examples/demo-site-intl/` is the second fixture (hreflang, mixed content, `lastmod`,
JS shell). It is checked by a unit test rather than by the CI script, and its expected
list lives in `examples/demo-site-intl/EXPECTED.md`.

**3. Validate the plugin and marketplace manifests.**

```bash
claude plugin validate .
```

Must pass with **no warnings**. This is easy to skip and easy to regret — it caught a
missing marketplace `description` before v1.0.0, which is the text users see when
browsing marketplaces. Warnings here are user-facing defects, not lint noise.

**4. Version numbers agree.** Bump `pyproject.toml`, `.claude-plugin/plugin.json` and the
`SKILL.md` frontmatter `version` together (a test fails if they differ) and add a
`CHANGELOG.md` entry, then:

```bash
claude plugin tag --dry-run
```

Use this as a **check, not as the tagging command.** It verifies that `plugin.json` and
the enclosing marketplace entry agree on the version, and that the working tree is
clean — both of which are what you actually want confirmed here.

It would create a tag named `seo-repair--v1.0.0`, which is the convention for a
monorepo shipping several plugins. This repo ships one plugin and tags plain `vX.Y.Z`
to match its GitHub releases. Don't mix the two: pick the plain form (below) and let
`--dry-run` do the validating.

**5. Nothing untracked is about to ship.**

```bash
git status --porcelain
```

Must be empty *after* running the tool, which proves `.gitignore` is still catching
crawl output. **A crawl artifact contains the full text of whatever site was audited.**
Never relax those ignore rules.

**6. Verify from a clean clone**, not from your working tree:

```bash
git clone . /tmp/verify && cd /tmp/verify
python tests/test_skill.py
```

This is what catches line-ending problems and files that only exist locally.

## Tagging and publishing

```bash
git tag -a vX.Y.Z -m "sitemend vX.Y.Z"
git push origin main --tags
gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <notes>
```

A pushed tag does **not** create a GitHub Release; that is a separate step.

**PyPI.** Pushing a `v*` tag runs `.github/workflows/release.yml`, which refuses a tag
that does not match `pyproject.toml` and then publishes through PyPI trusted publishing:
no API token is stored in the repo. One-time setup before the first tag, on pypi.org:
*Your projects -> Publishing -> Add a new pending publisher* with PyPI project name
`sitemend`, owner `SurendharVr`, repository `sitemend`, workflow `release.yml`,
environment `pypi` (and create a `pypi` environment under the repo's Settings ->
Environments). Until that exists the workflow
fails at the upload step; nothing else is affected. After the first successful upload,
add `pip install sitemend` to the README.

To check the wheel by hand without publishing anything:

```bash
python -m pip install build && python -m build
python -m venv /tmp/v && /tmp/v/bin/pip install dist/*.whl && /tmp/v/bin/sitemend --help
```

If your `gh` token lacks the `workflow` scope, pushing any change to
`.github/workflows/` is rejected outright and the whole push fails —
`gh auth refresh -h github.com -s workflow`.

## After publishing

**Confirm the plugin route actually resolves from the published repo**, not just from
disk. Local validation does not prove the marketplace clone works.

Do this in a throwaway directory with `--scope project` so your global config is never
touched:

```bash
mkdir /tmp/plugintest && cd /tmp/plugintest
claude plugin marketplace add <owner>/sitemend --scope project
claude plugin install seo-repair@seo-repair --scope project -y
claude plugin details seo-repair@seo-repair
```

Then clean up:

```bash
claude plugin uninstall seo-repair@seo-repair --scope project
claude plugin marketplace remove seo-repair --scope project
rm -rf ~/.claude/plugins/cache/seo-repair    # marketplace remove orphans this; prune does not clear it
```

### Do not leave the plugin installed on a development machine

Installing `seo-repair@<marketplace>` **shadows** a `seo-repair@skills-dir` copy in
`~/.claude/skills/`. Claude Code reports this clearly rather than failing:

> `seo-repair@skills-dir`: ✘ Not loaded — the name "seo-repair" is already taken by an
> installed plugin, which takes precedence.

Graceful, but the consequence is that the copy you edit stops being the copy that runs.
If your `~/.claude/skills/seo-repair` is the git checkout, uninstall the plugin when the
test is done.

## What CI does and does not prove

CI runs the unit tests and the demo-site end-to-end check on Linux, macOS and Windows
across Python 3.9 and 3.12.

It **excludes Lighthouse**, which needs a real browser. So `vitals.py` is not exercised
by CI on any platform, and stage-4 behaviour outside Windows is untested rather than
proven. Do not let a green check imply otherwise, and do not "fix" this by adding a job
that stubs Lighthouse — a passing test that did not run the thing is worse than a
documented gap.
