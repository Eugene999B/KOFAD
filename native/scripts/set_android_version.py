"""Set generated Capacitor Android release version without changing app identity.

Usage:
    python native/scripts/set_android_version.py android/app/build.gradle 1.0.0 10000
Never modifies a checked-in Gradle project, and rejects anything other than
simple semantic versions and strictly positive version codes.
"""
import re
import sys
from pathlib import Path


def set_android_version(source, version, version_code):
    if not isinstance(version, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]{0,2})\.(?:0|[1-9][0-9]{0,2})\.(?:0|[1-9][0-9]{0,3})", version
    ):
        raise ValueError("Release version must be a numeric X.Y.Z like 1.2.3")
    if isinstance(version_code, bool) or not isinstance(version_code, int) or not 1 <= version_code <= 2_100_000_000:
        raise ValueError("Version code must be a positive Android integer")
    code = re.sub(
        r"(?m)^(\s*versionCode\s+)\d+\s*$",
        lambda m: m[1] + str(version_code),
        source,
    )
    if code == source and not re.search(r"(?m)^\s*versionCode\s+" + str(version_code) + r"\s*$", source):
        raise ValueError("Generated Android Gradle file has no versionCode")
    updated = re.sub(
        r'(?m)^(\s*versionName\s+)"[^"]+"\s*$',
        lambda m: m[1] + '"' + version + '"',
        code,
    )
    if not re.search(r"(?m)^\s*versionName\s+\"" + re.escape(version) + r"\"\s*$", updated):
        raise ValueError("Generated Android Gradle file has no versionName")
    return updated


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: set_android_version.py android/app/build.gradle 1.0.0 10000")
    path = Path(sys.argv[1])
    original = path.read_text(encoding="utf8")
    updated = set_android_version(original, sys.argv[2], int(sys.argv[3]))
    path.write_text(updated, encoding="utf8")
    print("Updated generated native Android release version.")
