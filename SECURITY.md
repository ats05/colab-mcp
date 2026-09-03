<!-- Copyright 2026 Atsushi Onozawa. Licensed under Apache-2.0. -->

# Security policy

Please do not disclose credentials or exploitable security details in a public
issue. Use GitHub's private vulnerability reporting for this repository:

<https://github.com/ats05/colab-mcp/security/advisories/new>

If private vulnerability reporting is not available, open a public issue that
asks the maintainer for a private contact channel without including the
vulnerability details.

Include affected versions, reproduction steps, and the practical impact. Never
include a Colab MCP connection URL, bearer token, OAuth client secret, refresh
token, or notebook content that is not safe to share.

The Streamable HTTP and SSE transports are unauthenticated. They bind to
`127.0.0.1` by default. Non-loopback binding is rejected unless the operator
passes `--allow-insecure-non-loopback`, which explicitly acknowledges that
notebook tools and bearer connection credentials may become network-accessible.

Security updates are documented in CHANGELOG.md. No guaranteed response or
resolution timeline is currently offered.
