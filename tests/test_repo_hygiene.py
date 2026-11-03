"""
Guards against committing deployment artifacts and local settings files.

A deploy zip with a local.settings.json inside was once committed to this public
repo. Settings and build output belong in .gitignore, never in git.
"""
import fnmatch
import os
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FORBIDDEN_PATTERNS = [
    "*.zip",
    "*local.settings.json",
    "function-appsettings.json",
    ".env",
    "*/.env",
    ".env.*",
    "*/.env.*",
    "build/*",
    "dist/*",
    ".python_packages/*",
]
ALLOWED = {".env.example"}


def _tracked_files():
    if shutil.which("git") is None:
        pytest.skip("git not available")
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    return result.stdout.splitlines()


def test_no_build_artifacts_or_settings_files_are_committed():
    offenders = [
        path
        for path in _tracked_files()
        if path not in ALLOWED
        and any(fnmatch.fnmatch(path, pattern) for pattern in FORBIDDEN_PATTERNS)
    ]
    assert offenders == [], (
        "These files must not be committed (add them to .gitignore and "
        f"`git rm --cached` them): {offenders}"
    )
