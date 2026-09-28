#!/usr/bin/env python3
"""Bump the published version, commit the release, and create its Git tag."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIPE_FILE = ROOT / "pipe.yml"
README_FILE = ROOT / "README.md"
CONTRIBUTING_FILE = ROOT / "CONTRIBUTING.md"
CONTRACT_TEST_FILE = ROOT / "tests" / "test_runner.py"
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def git(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else ""


def parse_version(version: str) -> tuple[int, int, int, str | None, str | None]:
    match = SEMVER.fullmatch(version)
    if not match:
        raise ValueError(f"Not a valid semantic version: {version}")
    major, minor, patch = (int(match.group(i)) for i in range(1, 4))
    prerelease, build = match.group(4), match.group(5)
    if prerelease:
        for identifier in prerelease.split("."):
            if identifier.isdigit() and len(identifier) > 1 and identifier[0] == "0":
                raise ValueError(f"Invalid numeric prerelease identifier: {identifier}")
    return major, minor, patch, prerelease, build


def render(parts: tuple[int, int, int, str | None, str | None]) -> str:
    major, minor, patch, prerelease, build = parts
    value = f"{major}.{minor}.{patch}"
    if prerelease:
        value += f"-{prerelease}"
    if build:
        value += f"+{build}"
    return value


def bump(current: str, requested: str) -> str:
    major, minor, patch, prerelease, _build = parse_version(current)
    if requested not in {"major", "minor", "patch", "premajor", "preminor", "prepatch", "prerelease"}:
        return render(parse_version(requested.removeprefix("v")))

    if requested == "major":
        major, minor, patch, prerelease = major + 1, 0, 0, None
    elif requested == "minor":
        minor, patch, prerelease = minor + 1, 0, None
    elif requested == "patch":
        patch, prerelease = patch + 1, None
    elif requested == "premajor":
        major, minor, patch, prerelease = major + 1, 0, 0, "0"
    elif requested == "preminor":
        minor, patch, prerelease = minor + 1, 0, "0"
    elif requested == "prepatch":
        patch, prerelease = patch + 1, "0"
    else:  # prerelease
        if prerelease is None:
            patch += 1
            prerelease = "0"
        else:
            identifiers = prerelease.split(".")
            numeric_index = next(
                (i for i in range(len(identifiers) - 1, -1, -1)
                 if identifiers[i].isdigit()),
                None,
            )
            if numeric_index is None:
                identifiers.append("0")
            else:
                identifiers[numeric_index] = str(int(identifiers[numeric_index]) + 1)
            prerelease = ".".join(identifiers)
    return render((major, minor, patch, prerelease, None))


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: python {Path(__file__).as_posix()} <newversion|major|minor|patch|premajor|preminor|prepatch|prerelease>", file=sys.stderr)
        return 2

    if git("status", "--porcelain", capture=True):
        print("Refusing to release with a dirty working tree.", file=sys.stderr)
        return 2

    pipe_text = PIPE_FILE.read_text(encoding="utf-8")
    image_match = re.search(r"(?m)^image:\s+\S+:(\S+)\s*$", pipe_text)
    if not image_match:
        print("Could not read the current version from pipe.yml.", file=sys.stderr)
        return 2

    try:
        old_version = render(parse_version(image_match.group(1)))
        new_version = bump(old_version, sys.argv[1])
        if parse_version(new_version)[4] is not None:
            raise ValueError("Docker image versions cannot include build metadata (+...)")
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2

    if new_version == old_version:
        print(f"Version is already {new_version}.", file=sys.stderr)
        return 2

    new_tag = f"v{new_version}"
    if subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{new_tag}"], cwd=ROOT).returncode == 0:
        print(f"Tag {new_tag} already exists.", file=sys.stderr)
        return 2

    old_major = parse_version(old_version)[0]
    new_major = parse_version(new_version)[0]
    replacements = {
        PIPE_FILE: (old_version, new_version),
        README_FILE: (old_version, new_version),
        CONTRACT_TEST_FILE: (f"api-mitigation-test:{old_version}", f"api-mitigation-test:{new_version}"),
    }
    for path, (old, new) in replacements.items():
        contents = path.read_text(encoding="utf-8")
        updated = contents.replace(old, new)
        path.write_text(updated, encoding="utf-8", newline="\n")

    for path in (README_FILE, CONTRIBUTING_FILE):
        contents = path.read_text(encoding="utf-8")
        updated = re.sub(
            rf"(?<![\w.])v{old_major}(?![\w.])",
            f"v{new_major}",
            contents,
        )
        path.write_text(updated, encoding="utf-8", newline="\n")

    git("add", "pipe.yml", "README.md", "CONTRIBUTING.md", "tests/test_runner.py")
    git("commit", "-m", f"Release {new_version}")
    git("tag", "-a", new_tag, "-m", f"Release {new_version}")
    print(f"Created release commit and tag {new_tag}.")
    print(f"Push with: git push origin HEAD && git push origin {new_tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
