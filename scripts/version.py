#!/usr/bin/env python3
"""Bump the published version, commit the release, and create its Git tag."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_FILE = ROOT / "package.json"
IMAGE = "ppwcode/api-mitigation-test"
IMAGE_REFERENCE = re.compile(
    rf"(?<![\w./-])(?P<prefix>docker://)?{re.escape(IMAGE)}:"
    r"(?P<version>[0-9A-Za-z_.+-]+)"
)
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


def update_image_references(contents: str, new_version: str) -> str:
    def replace(match: re.Match[str]) -> str:
        try:
            parse_version(match.group("version"))
        except ValueError:
            return match.group(0)
        return f"{match.group('prefix') or ''}{IMAGE}:{new_version}"

    return IMAGE_REFERENCE.sub(replace, contents)


def update_tracked_files(new_version: str) -> list[str]:
    changed = []
    for filename in git("ls-files", "-z", capture=True).split("\0"):
        if not filename:
            continue
        path = ROOT / filename
        if path.is_symlink() or not path.is_file():
            continue
        original = path.read_bytes()
        if b"\0" in original:
            continue
        try:
            contents = original.decode("utf-8")
        except UnicodeDecodeError:
            continue
        updated = update_image_references(contents, new_version)
        if updated != contents:
            path.write_bytes(updated.encode("utf-8"))
            changed.append(filename)
    return changed


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: python {Path(__file__).as_posix()} <newversion|major|minor|patch|premajor|preminor|prepatch|prerelease>", file=sys.stderr)
        return 2

    if git("status", "--porcelain", capture=True):
        print("Refusing to release with a dirty working tree.", file=sys.stderr)
        return 2

    try:
        package = json.loads(PACKAGE_FILE.read_text(encoding="utf-8"))
        if not isinstance(package, dict) or not isinstance(package.get("version"), str):
            raise ValueError("package.json must contain a string version field")
        old_version = render(parse_version(package["version"]))
        new_version = bump(old_version, sys.argv[1])
        if parse_version(new_version)[4] is not None:
            raise ValueError("Docker image versions cannot include build metadata (+...)")
    except OSError as error:
        print(f"Could not read the current version from package.json: {error}", file=sys.stderr)
        return 2
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

    changed = update_tracked_files(new_version)
    # Reload to preserve any image references updated inside package.json itself.
    package = json.loads(PACKAGE_FILE.read_text(encoding="utf-8"))
    package["version"] = new_version
    PACKAGE_FILE.write_text(
        json.dumps(package, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    git("add", "--", "package.json", *changed)
    git("commit", "-m", f"Release {new_version}")
    git("tag", "-a", new_tag, "-m", f"Release {new_version}")
    print(f"Created release commit and tag {new_tag}.")
    print(f"Push with: git push origin HEAD && git push origin {new_tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
