import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import version


@pytest.mark.parametrize("tag", ["0.2.2", "1.2.3", "1.2.3-rc.1", "1.2.3+build.4"])
def test_updates_pinned_image_versions(tag):
    reference = f"{version.IMAGE}:{tag}"
    contents = f'image: {reference}\r\n- pipe: docker://{reference}\r\n'
    assert version.update_image_references(contents, "2.0.0") == (
        f'image: {version.IMAGE}:2.0.0\r\n'
        f'- pipe: docker://{version.IMAGE}:2.0.0\r\n'
    )


@pytest.mark.parametrize("reference", [
    f"{version.IMAGE}:<version>",
    f"{version.IMAGE}:local",
    f"{version.IMAGE}:1",
    f"{version.IMAGE}:01.2.3",
    f"{version.IMAGE}:1.2.3-01",
    f"{version.IMAGE}:1.2.3.4",
    f"other/{version.IMAGE}:1.2.3",
    "other/image:1.2.3",
    "unrelated version 1.2.3 and v1",
])
def test_preserves_other_references(reference):
    assert version.update_image_references(reference, "2.0.0") == reference


@pytest.mark.parametrize("pipe_version", ["0.2.2", "0.1.0", None])
def test_release_discovers_and_commits_tracked_references(tmp_path, pipe_version):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True,
        ).stdout.strip()

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    script = tmp_path / "scripts" / "version.py"
    script.parent.mkdir()
    script.write_bytes(Path(version.__file__).read_bytes())
    check_tag = script.with_name("check_tag.py")
    check_tag.write_bytes(Path(version.__file__).with_name("check_tag.py").read_bytes())
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        f"# Changelog\n\n## Unreleased\n\n### Features\n\n- New feature.\n\n"
        f"## 0.2.2\n\n- Use {version.IMAGE}:0.2.2.\n"
    )
    package = {
        "name": "api-mitigation-test", "version": "0.2.2", "private": True,
        "scripts": {"image": f"docker run {version.IMAGE}:0.1.0"},
        "description": "Unrelated version 0.2.2",
    }
    metadata = tmp_path / "package.json"
    metadata.write_text(json.dumps(package, indent=2) + "\n")
    if pipe_version is not None:
        (tmp_path / "pipe.yml").write_text(f"image: {version.IMAGE}:{pipe_version}\n")
    nested = tmp_path / "docs" / "new-guide.md"
    nested.parent.mkdir()
    nested.write_bytes(f"docker://{version.IMAGE}:0.1.0\r\nUnrelated 0.2.2\r\n".encode())
    (tmp_path / "binary.dat").write_bytes(f"\0{version.IMAGE}:0.2.2".encode())
    (tmp_path / "non-utf8.txt").write_bytes(f"{version.IMAGE}:0.2.2".encode() + b"\xff")
    (tmp_path / ".gitignore").write_text("ignored.txt\n")
    git("add", ".")
    git("commit", "-m", "Initial")
    ignored = tmp_path / "ignored.txt"
    ignored.write_text(f"{version.IMAGE}:0.2.2")

    subprocess.run([sys.executable, str(script), "patch"], cwd=tmp_path, check=True)

    expected_package = dict(package, version="0.2.3")
    expected_package["scripts"] = {"image": f"docker run {version.IMAGE}:0.2.3"}
    assert json.loads(metadata.read_text()) == expected_package
    if pipe_version is not None:
        assert (tmp_path / "pipe.yml").read_text() == f"image: {version.IMAGE}:0.2.3\n"
    assert nested.read_bytes() == f"docker://{version.IMAGE}:0.2.3\r\nUnrelated 0.2.2\r\n".encode()
    assert ignored.read_text() == f"{version.IMAGE}:0.2.2"
    assert (tmp_path / "binary.dat").read_bytes() == f"\0{version.IMAGE}:0.2.2".encode()
    assert (tmp_path / "non-utf8.txt").read_bytes() == f"{version.IMAGE}:0.2.2".encode() + b"\xff"
    assert "## 0.2.3\n" in changelog.read_text()
    assert "Unreleased" not in changelog.read_text()
    assert f"{version.IMAGE}:0.2.2" in changelog.read_text()
    expected_files = ["CHANGELOG.md", "docs/new-guide.md", "package.json"]
    if pipe_version is not None:
        expected_files.append("pipe.yml")
    assert git("show", "--format=", "--name-only", "HEAD").splitlines() == expected_files
    assert git("tag", "--list") == "v0.2.3"
    assert git("status", "--porcelain") == ""

    notes_file = tmp_path / "release-notes.md"
    env = dict(os.environ, RELEASE_TAG="v0.2.3", RELEASE_NOTES_FILE=str(notes_file))
    subprocess.run([sys.executable, str(check_tag)], cwd=tmp_path, env=env, check=True)
    assert notes_file.read_text() == "### Features\n\n- New feature.\n"
    env["RELEASE_TAG"] = "v0.2.4"
    assert subprocess.run([sys.executable, str(check_tag)], cwd=tmp_path, env=env).returncode == 2


@pytest.mark.parametrize("current", [
    None, "not-json", "[]", "null", "{}", '{"version": null}', '{"version": 123}',
    '{"version": "not-a-version"}', '{"version": "01.2.3"}',
])
def test_rejects_missing_or_invalid_package_version(tmp_path, monkeypatch, capsys, current):
    metadata = tmp_path / "package.json"
    if current is not None:
        metadata.write_text(current)
    monkeypatch.setattr(version, "PACKAGE_FILE", metadata)
    monkeypatch.setattr(sys, "argv", ["scripts/version.py", "patch"])
    monkeypatch.setattr(version, "git", lambda *args, **kwargs: "")
    monkeypatch.setattr(version, "update_tracked_files", lambda *_: pytest.fail("Unexpected file update"))

    assert version.main() == 2
    assert capsys.readouterr().err
    if current is None:
        assert not metadata.exists()
    else:
        assert metadata.read_text() == current


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_extracts_only_matching_release_notes(newline):
    changelog = newline.join([
        "<!-- License -->", "# Changelog", "## Unreleased", "- Future.",
        "## 1.2.30", "- Different version.", "## 1.2.3 - 2026-10-16",
        "### Features", "", "- Released. <!-- internal comment -->",
        "## 1.2.2", "- Previous.",
    ])
    assert version.release_notes(changelog, "1.2.3").replace("\r\n", "\n") == (
        "### Features\n\n- Released.\n"
    )


@pytest.mark.parametrize("changelog", [
    "# Changelog\n", "## 1.2.30\n- Wrong version.\n",
    "## 1.2.3\n### Features\n<!-- TODO -->\n",
    "## 1.2.3\n- First.\n## 1.2.3\n- Duplicate.\n",
])
def test_rejects_missing_empty_or_duplicate_release_notes(changelog):
    with pytest.raises(ValueError):
        version.release_notes(changelog, "1.2.3")


@pytest.mark.parametrize("changelog", [
    "## Unreleased\n- First.\n## Unreleased\n- Duplicate.\n",
    "## Unreleased\n- New.\n## 1.2.3\n- Existing.\n",
    "## Unreleased\n### Features\n<!-- TODO -->\n",
])
def test_rejects_invalid_changelog_promotion(changelog):
    with pytest.raises(ValueError):
        version.prepare_changelog(changelog, "1.2.3")


def test_accepts_prepared_version_section():
    changelog = "# Changelog\n\n## 1.2.3\n\n- Prepared notes.\n"
    assert version.prepare_changelog(changelog, "1.2.3") == changelog


def test_invalid_changelog_does_not_modify_release_files(tmp_path, monkeypatch):
    package = tmp_path / "package.json"
    package.write_text('{"version": "1.0.0"}\n')
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## Unreleased\n")
    monkeypatch.setattr(version, "PACKAGE_FILE", package)
    monkeypatch.setattr(version, "CHANGELOG_FILE", changelog)
    monkeypatch.setattr(version, "ROOT", tmp_path)
    monkeypatch.setattr(version, "git", lambda *args, **kwargs: "")
    monkeypatch.setattr(sys, "argv", ["scripts/version.py", "patch"])
    monkeypatch.setattr(version, "update_tracked_files", lambda *_: pytest.fail("Unexpected file update"))

    assert version.main() == 2
    assert package.read_text() == '{"version": "1.0.0"}\n'
    assert changelog.read_text() == "# Changelog\n\n## Unreleased\n"
