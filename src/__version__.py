"""Version information for SIPSTACK Connector Asterisk."""

import os
from pathlib import Path

# Read version from VERSION file
VERSION_FILE = Path(__file__).parent.parent / "VERSION"

try:
    __version__ = VERSION_FILE.read_text().strip()
except FileNotFoundError:
    __version__ = "0.1.0"  # Fallback version

# Version components
VERSION_INFO = tuple(int(x) for x in __version__.split('.'))
MAJOR, MINOR, PATCH = VERSION_INFO

# Full version string for display
VERSION_STRING = f"SIPSTACK Connector Asterisk v{__version__}"