#!/usr/bin/env python3
"""Validate the release tag and extract its changelog section for GitHub."""

import json
import os
from pathlib import Path
import sys

from version import CHANGELOG_FILE, PACKAGE_FILE, parse_version, release_notes


def main() -> int:
    try:
        version = json.loads(PACKAGE_FILE.read_text(encoding="utf-8"))["version"]
        parse_version(version)
        if os.environ.get("RELEASE_TAG") != f"v{version}":
            raise ValueError(f"RELEASE_TAG must exactly match v{version}")
        notes = release_notes(CHANGELOG_FILE.read_text(encoding="utf-8"), version)
        Path(os.environ["RELEASE_NOTES_FILE"]).write_text(notes, encoding="utf-8", newline="\n")
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(error, file=sys.stderr)
        return 2
    print(f"Extracted release notes for {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
