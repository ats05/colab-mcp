<!-- Modified by Atsushi Onozawa, 2026. Licensed under Apache-2.0. -->

# Contributing

Issues that include a minimal reproduction and redacted diagnostics are
welcome. For a substantial code or behavior change, open an issue before a pull
request so scope, compatibility, and upstream provenance can be agreed first.

Before submitting a change:

1. Preserve existing Google, Sebastian, and Atsushi copyright and license
   notices. Add a clear modification notice when changing an upstream-derived
   file.
2. Do not commit Colab connection URLs, bearer tokens, OAuth credentials,
   notebook contents, local machine paths, or other private information.
3. Run `uv run ruff check .`, `uv run pytest`, and
   `uv run python scripts/e2e_smoke.py`.
4. Update tests and documentation for user-visible behavior.
5. Keep changes focused and explain compatibility or migration effects.

Unless explicitly stated otherwise, contributions intentionally submitted to
this repository are provided under the Apache License, Version 2.0, consistent
with [LICENSE](LICENSE). See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md),
[SECURITY.md](SECURITY.md), and [SUPPORT.md](SUPPORT.md).

For questions about the official project, use the official
[`googlecolab/colab-mcp`](https://github.com/googlecolab/colab-mcp) repository.
This standalone fork is maintained independently and is not endorsed by Google.
