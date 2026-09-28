from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r"^(?:v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?|build-[0-9a-f]{40})$")
EXACT_SOURCE_FILES = {
    "backend/pyproject.toml",
    "examples/sample-race.csv",
    "examples/sample-results.csv",
    "scripts/start-backend.ps1",
}
ALLOWED_SOURCE_PREFIXES = ("backend/app/",)
RELEASE_README_SOURCE = "docs/release-package.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a local-app release ZIP from an explicit source allowlist.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output-directory", default="dist")
    return parser.parse_args()


def is_allowed_source(path: str) -> bool:
    return path in EXACT_SOURCE_FILES or path == RELEASE_README_SOURCE or path.startswith(ALLOWED_SOURCE_PREFIXES)


def is_forbidden_package_path(path: str) -> bool:
    parts = tuple(part.lower() for part in PurePosixPath(path).parts)
    name = parts[-1]
    if any(part in {".local", ".git", ".venv", "node_modules"} for part in parts):
        return True
    if name in {".env", ".dev.vars"} or (name.startswith(".env.") and name != ".env.example"):
        return True
    forbidden_suffixes = (
        ".sqlite", ".sqlite3", ".sqlite-wal", ".sqlite-shm", ".sqlite-journal",
        ".sqlite3-wal", ".sqlite3-shm", ".sqlite3-journal",
        ".db", ".db-wal", ".db-shm", ".db-journal", ".bak", ".backup",
    )
    return name.endswith(forbidden_suffixes)


def main() -> None:
    args = parse_args()
    if not VERSION_PATTERN.fullmatch(args.version):
        raise SystemExit("Version must be a vX.Y.Z tag or build-<40 character commit SHA>.")
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_sha):
        raise SystemExit("Source SHA must be a full 40 character Git commit SHA.")

    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    if head_sha != args.source_sha:
        raise SystemExit(f"Source SHA mismatch: requested {args.source_sha}, checkout is {head_sha}.")
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=PROJECT_ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    if status:
        raise SystemExit("Refusing to package a dirty checkout; build from the exact committed source.")

    frontend_dist = PROJECT_ROOT / "frontend" / "dist"
    if not (frontend_dist / "index.html").is_file():
        raise SystemExit("Build frontend/dist before packaging the release.")
    output_directory = Path(args.output_directory)
    if not output_directory.is_absolute():
        output_directory = PROJECT_ROOT / output_directory
    output_directory.mkdir(parents=True, exist_ok=True)
    if any(output_directory.iterdir()):
        raise SystemExit("Output directory must be empty so a stale package cannot be reused.")

    archive_result = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=PROJECT_ROOT, check=True, capture_output=True,
    )
    archive_name = f"horse-racing-analytics-{args.version}.zip"
    archive_path = output_directory / archive_name
    with tempfile.TemporaryDirectory(prefix="hra-release-") as temp_directory:
        package_root = Path(temp_directory) / f"horse-racing-analytics-{args.version}"
        package_root.mkdir()
        with tarfile.open(fileobj=BytesIO(archive_result.stdout), mode="r:") as source_archive:
            for member in source_archive.getmembers():
                source_path = PurePosixPath(member.name)
                source_name = source_path.as_posix()
                if source_path.is_absolute() or ".." in source_path.parts or not is_allowed_source(source_name):
                    continue
                if not member.isfile():
                    continue
                if is_forbidden_package_path(source_name):
                    raise SystemExit(f"Refusing to package a local secret or database file: {source_name}")
                content = source_archive.extractfile(member)
                if content is None:
                    raise SystemExit(f"Could not read tracked source file: {source_name}")
                output_name = "README.md" if source_name == RELEASE_README_SOURCE else source_name
                destination = package_root.joinpath(*PurePosixPath(output_name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content.read())

        packaged_dist = package_root / "frontend" / "dist"
        if packaged_dist.exists():
            shutil.rmtree(packaged_dist)
        shutil.copytree(frontend_dist, packaged_dist)

        manifest = {
            "version": args.version,
            "source_sha": args.source_sha,
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
            "node_version": (PROJECT_ROOT / ".nvmrc").read_text(encoding="utf-8").strip(),
        }
        (package_root / "release-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        required_files = (
            "README.md", "backend/pyproject.toml", "backend/app/main.py",
            "frontend/dist/index.html", "scripts/start-backend.ps1",
            "examples/sample-race.csv", "examples/sample-results.csv",
        )
        for required_path in required_files:
            if not (package_root / required_path).is_file():
                raise SystemExit(f"Release package is missing required file: {required_path}")

        file_paths = sorted(path for path in package_root.rglob("*") if path.is_file())
        for path in file_paths:
            relative_path = path.relative_to(package_root).as_posix()
            if is_forbidden_package_path(relative_path):
                raise SystemExit(f"Refusing to package a local secret or database file: {relative_path}")

        with ZipFile(archive_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as output_archive:
            for path in file_paths:
                relative_path = path.relative_to(package_root).as_posix()
                info = ZipInfo(f"{package_root.name}/{relative_path}", date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                output_archive.writestr(info, path.read_bytes())

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path = output_directory / f"{archive_name}.sha256"
    checksum_path.write_text(f"{digest}  {archive_name}\n", encoding="ascii")
    print(f"Created {archive_path.name} ({len(file_paths)} files, SHA-256 {digest}).")


if __name__ == "__main__":
    main()
