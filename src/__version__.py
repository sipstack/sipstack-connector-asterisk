"""Version information for SIPSTACK Connector Asterisk."""

import os
from pathlib import Path

# Read version from VERSION file
# Try multiple paths to handle different runtime environments
possible_paths = [
    Path(__file__).parent.parent / "VERSION",  # Development
    Path("/app/VERSION"),  # Docker container
    Path("VERSION"),  # Current directory
]

__version__ = "0.1.0"  # Default fallback
for version_path in possible_paths:
    try:
        if version_path.exists():
            __version__ = version_path.read_text().strip()
            break
    except Exception:
        continue

# Version components
VERSION_INFO = tuple(int(x) for x in __version__.split('.'))
MAJOR, MINOR, PATCH = VERSION_INFO

# Full version string for display
VERSION_STRING = f"SIPSTACK Connector Asterisk v{__version__}"