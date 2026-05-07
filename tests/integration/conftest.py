"""Integration-layer shared fixtures and session hooks.

Patches Python's `ssl` module (via the `truststore` package) to verify TLS using
the operating system certificate store rather than `certifi`'s vendored bundle.
This Just Works behind corporate TLS-intercepting proxies whose root CA is
installed in the OS store but not in certifi, and is harmless on machines whose
OS store mirrors certifi (the common case). Symmetric with `regenerate.py`,
which does the same unconditionally.
"""

from __future__ import annotations

import truststore

truststore.inject_into_ssl()
