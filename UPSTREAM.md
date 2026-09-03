<!-- Copyright 2026 Atsushi Onozawa. Licensed under Apache-2.0. -->

# Upstream and provenance

This repository is an unofficial, community-maintained standalone fork of the
official [`googlecolab/colab-mcp`](https://github.com/googlecolab/colab-mcp)
project. It also includes work from Sebastian Gil Pinzon's
[`SebastianGilPinzon/colab-mcp`](https://github.com/SebastianGilPinzon/colab-mcp)
fork. Later connection handoff, execution tracking, shared transport, safety,
testing, and documentation changes were made by Atsushi Onozawa.

The repository is not attached to GitHub's fork network. Upstream changes must
therefore be reviewed and integrated manually. Existing Google and Sebastian
copyright notices must be retained. Files changed after their upstream version
carry an additional modification notice; files first introduced by Atsushi
carry Atsushi's copyright notice.

Files added or modified in Sebastian's fork are marked with Sebastian's
copyright or an explicit fork-addition/modification notice. Files subsequently
changed by Atsushi retain those notices and add Atsushi's modification notice.

The project is distributed under Apache-2.0. See [LICENSE](LICENSE) and
[NOTICE](NOTICE). The license does not grant trademark rights. This project is
not affiliated with, sponsored by, or endorsed by Google.

## Updating from upstream

1. Fetch both the official `upstream` remote and the intermediate `sebastian`
   remote.
2. Review provenance, license notices, behavior changes, and dependency changes
   before merging or cherry-picking.
3. Preserve existing notices and add a modification notice to files changed in
   this fork.
4. Update CHANGELOG.md, regenerate `uv.lock`, and run the full release checks in
   RELEASING.md.
