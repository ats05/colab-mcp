<!-- Copyright 2026 Atsushi Onozawa. Licensed under Apache-2.0. -->

# Release checklist

This project is not currently published to PyPI. Do not publish the
`colab-mcp` distribution name without first confirming ownership and avoiding
confusion with the official project.

Before a source release or visibility change:

- [ ] Confirm the version and dated CHANGELOG entry.
- [ ] Review the diff against official and Sebastian upstreams; preserve all
      copyright and license notices.
- [ ] Run `uv lock --check`, `uv run ruff check .`, `uv run pytest`, and
      `uv run python scripts/e2e_smoke.py`.
- [ ] Run `git diff --check` and verify a clean generated lockfile.
- [ ] Run official `gitleaks git --redact` (or an equivalently maintained
      scanner) against every branch and tag intended for publication. A release
      and visibility change require zero findings across every public ref and a
      clean reachable history; scanning only the current tree is insufficient.
- [ ] Confirm README, NOTICE, UPSTREAM.md, SECURITY.md, and SUPPORT.md match the
      release.
- [ ] Review dependency licenses and create third-party notices or an SBOM for
      any wheel, container, binary, hosted service, or paid bundle.
- [ ] Confirm only intended branches and tags will be public.
- [ ] Before changing visibility, review the repository security settings and
      prepare a main-branch ruleset requiring the CI checks. Confirm whether
      private vulnerability reporting, Dependabot alerts/security updates,
      secret scanning, and push protection are available for the current
      visibility and account.
- [ ] Verify the release from a fresh clone before creating a tag.

Immediately after changing repository visibility:

- [ ] Enable and test private vulnerability reporting; verify the private
      advisory link in SECURITY.md reaches the confidential report form.
- [ ] Enable the prepared main-branch protection/ruleset, require pull requests
      and passing CI, and prevent force-pushes and branch deletion.
- [ ] Enable Dependabot alerts and security updates, then confirm
      `.github/dependabot.yml` is active.
- [ ] Enable secret scanning and push protection, then review any historical
      alerts before announcing the repository.
- [ ] Recheck the public branch/tag list and clone the repository without
      credentials to verify the published instructions.
