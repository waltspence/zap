"""zap — a terminal-native, agent-friendly app uninstaller.

Finds an installed application plus all of its leftover files (preferences,
caches, application support, containers, launch agents, saved state, logs,
receipts) and removes them recoverably. Cross-platform (macOS + Linux),
zero runtime dependencies.
"""

__version__ = "0.1.0"

# Version of the machine-readable (--json) output envelope. Bump on any
# backwards-incompatible change to the JSON schema so agents can pin it.
SCHEMA_VERSION = 1

__all__ = ["__version__", "SCHEMA_VERSION"]
