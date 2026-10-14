#!/usr/bin/env python3
"""
DeskSweep — clean up a messy folder in one command.

Two subcommands:

    python desksweep.py organize [folder]   sort files into type-based subfolders
    python desksweep.py dupes [folder]      find duplicate files wasting disk space

Organize features:
  * Sorts top-level files by extension into subfolders: Images, Documents,
    Videos, Audio, Archives, Code, Other.
  * --dry-run shows the plan without moving anything (the safe default habit).
  * Name collisions are auto-renamed ("photo (1).jpg") instead of overwritten.
  * Every real run writes an undo log, so `organize --undo` puts files back.
  * Hidden files (".*"), system junk (Thumbs.db, desktop.ini) are skipped.

Dupes features:
  * Groups by file size first (cheap), then hashes only same-size files with
    SHA-256 in 1 MB chunks (expensive) — so big unique files cost one stat()
    instead of a full hash.
  * Reports duplicate groups sorted by wasted space, with human-readable sizes.
  * --delete enables interactive per-group deletion (keeps the first file,
    asks "y/N" for each group, defaults to NO — never deletes unprompted).

Pure Python standard library — no dependencies, runs anywhere Python 3.8+.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# File-type categories
# ---------------------------------------------------------------------------
# Extension -> category. Anything not listed falls into "Other".
# Deliberately a flat, human-readable mapping rather than magic-number
# sniffing: extensions are what users actually organize by, and sniffing
# bytes can't tell a .docx from a .zip (both are ZIP containers) anyway.

CATEGORIES = {
    "Images": {
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg",
        ".tiff", ".tif", ".ico", ".heic", ".avif", ".psd",
    },
    "Documents": {
        ".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".xls", ".xlsx",
        ".csv", ".ppt", ".pptx", ".md", ".epub", ".pages", ".numbers",
    },
    "Videos": {
        ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".flv", ".m4v",
    },
    "Audio": {
        ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma",
    },
    "Archives": {
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso",
    },
    "Code": {
        ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".hpp", ".html",
        ".css", ".json", ".xml", ".sh", ".yaml", ".yml", ".go", ".rs",
        ".kt", ".sql", ".ipynb",
    },
}

# Files that must never be touched by organize or dupes, even though some
# of them have recognized extensions (Thumbs.db has none; desktop.ini has).
SYSTEM_FILES = {"thumbs.db", "desktop.ini", ".ds_store"}

# Where undo logs live (outside the folder being swept, so a re-sweep
# never tries to organize the logs themselves).
UNDO_DIR = Path.home() / ".desksweep" / "undo-logs"

CHUNK_SIZE = 1024 * 1024  # 1 MB — hash in chunks so huge files don't eat RAM


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def category_for(path: Path) -> str:
    """Return the category folder name for a file based on its extension."""
    ext = path.suffix.lower()
    for category, extensions in CATEGORIES.items():
        if ext in extensions:
            return category
    return "Other"


def should_skip(entry: os.DirEntry) -> bool:
    """True if this entry must never be touched (hidden or system file)."""
    name = entry.name
    return name.startswith(".") or name.lower() in SYSTEM_FILES


def human_size(num_bytes: int) -> str:
    """Format a byte count like '1.4 MB' (binary 1024-based units)."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(num_bytes)} B"
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"  # pragma: no cover (unreachable)


def unique_dest(dest_dir: Path, filename: str) -> Path:
    """Return a non-colliding destination path, adding ' (n)' if needed.

    Never overwrites an existing file: photo.jpg -> photo (1).jpg ->
    photo (2).jpg, ...
    """
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    stem, suffix = os.path.splitext(filename)
    n = 1
    while True:
        candidate = dest_dir / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


# ---------------------------------------------------------------------------
# organize — sort a folder into type subfolders, with dry-run and undo
# ---------------------------------------------------------------------------

def plan_organize(folder: Path):
    """Build the move plan: list of (source, dest) tuples for top-level files.

    Top-level only (not recursive): organizing should never reach into
    subfolders the user already arranged, and the category subfolders we
    create are themselves skipped so re-running is a no-op.
    """
    moves = []
    skipped = 0
    category_names = set(CATEGORIES) | {"Other"}

    with os.scandir(folder) as it:
        for entry in it:
            # Directories are left alone entirely — DeskSweep sorts loose
            # files, never rearranges a user's folder structure.
            if entry.is_dir(follow_symlinks=False):
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            if should_skip(entry):
                skipped += 1
                continue
            src = Path(entry.path)
            dest_dir = folder / category_for(src)
            dest = unique_dest(dest_dir, src.name)
            # Skip files already sitting in their own category folder.
            # (unique_dest would return the same name when nothing collides,
            # so "already there" is just dest == src.)
            if dest == src:
                skipped += 1
                continue
            moves.append((src, dest))

    return moves, skipped


def run_organize(folder: Path, dry_run: bool) -> int:
    """Execute (or preview) the organize plan. Returns 0 on success."""
    if not folder.is_dir():
        print(f"Error: '{folder}' is not a folder.", file=sys.stderr)
        return 1

    moves, skipped = plan_organize(folder)

    if not moves:
        print(f"Nothing to organize in {folder} "
              f"({skipped} file(s) already tidy or skipped).")
        return 0

    # Group the plan by destination folder for a readable preview.
    by_category = {}
    for src, dest in moves:
        by_category.setdefault(dest.parent.name, []).append((src, dest))

    for category in sorted(by_category):
        print(f"\n{category}/")
        for src, dest in sorted(by_category[category]):
            rename_note = f"  -> renamed to {dest.name}" if dest.name != src.name else ""
            print(f"  {src.name}{rename_note}")

    print(f"\n{len(moves)} file(s) to move, {skipped} skipped.")

    if dry_run:
        print("Dry run — nothing was moved. Drop --dry-run to apply.")
        return 0

    undo_log = []
    for src, dest in moves:
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dest)  # atomic on the same filesystem; no partial copies
        undo_log.append({"src": str(src), "dst": str(dest)})

    # Persist the undo log so `organize --undo` can reverse this run.
    UNDO_DIR.mkdir(parents=True, exist_ok=True)
    log_path = UNDO_DIR / f"undo-{datetime.now():%Y%m%d-%H%M%S}.json"
    log_path.write_text(json.dumps(
        {"folder": str(folder), "ran_at": time.time(), "moves": undo_log},
        indent=2,
    ), encoding="utf-8")

    print(f"\nDone — moved {len(moves)} file(s). Undo log: {log_path}")
    print("Tip: run `organize --undo` to put everything back.")
    return 0


def run_undo(folder: Path) -> int:
    """Reverse the most recent organize run that touched this folder."""
    if not UNDO_DIR.is_dir():
        print("No undo logs found — nothing to undo.")
        return 0

    logs = sorted(UNDO_DIR.glob("undo-*.json"))
    # Find the newest log whose recorded folder matches.
    target = None
    for log_path in reversed(logs):
        try:
            data = json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if Path(data.get("folder", "")) == folder:
            target = (log_path, data)
            break

    if target is None:
        print(f"No undo log found for {folder} — nothing to undo.")
        return 0

    log_path, data = target
    restored, problems = 0, []
    # Reverse the move order so re-created subfolders unwind cleanly.
    for move in reversed(data["moves"]):
        src, dst = Path(move["src"]), Path(move["dst"])
        if not dst.exists():
            problems.append(f"missing: {dst} (already moved or deleted?)")
            continue
        if src.exists():
            problems.append(f"blocked: {src} already exists at original spot")
            continue
        src.parent.mkdir(parents=True, exist_ok=True)
        dst.rename(src)
        restored += 1

    # Only delete the log once everything restored cleanly; otherwise keep
    # it so the user can re-run undo after fixing the leftovers manually.
    if not problems:
        log_path.unlink()
        print(f"Undone — restored {restored} file(s).")
    else:
        print(f"Restored {restored} file(s), {len(problems)} problem(s):")
        for p in problems:
            print(f"  ! {p}")
        print(f"Undo log kept at {log_path} — re-run --undo after fixing.")
    return 0


# ---------------------------------------------------------------------------
# dupes — find duplicate files, report wasted space, optional delete
# ---------------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    """SHA-256 of a file, streamed in 1 MB chunks (constant memory)."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def find_duplicates(folder: Path):
    """Return duplicate groups: list of (size, hash, [paths]).

    Two-pass design for speed:
      1. Group by size — one stat() per file, pure metadata. Files with a
         unique size can't have duplicates, so they never get hashed.
      2. Only same-size groups get SHA-256'd. Same size + same hash =>
         true duplicates (hash collision risk is negligible for SHA-256).
    Hidden files/dirs and system files are skipped, and symlinks are
    never followed (they aren't real disk usage).
    """
    by_size = {}
    for root, dirs, files in os.walk(folder):
        # Prune hidden directories (e.g. .git) before descending into them.
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.startswith(".") or name.lower() in SYSTEM_FILES:
                continue
            path = Path(root) / name
            if path.is_symlink() or not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue  # unreadable/racy file — skip, don't crash
            by_size.setdefault(size, []).append(path)

    groups = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        by_hash = {}
        for path in paths:
            try:
                file_hash = sha256_of(path)
            except OSError:
                continue
            by_hash.setdefault(file_hash, []).append(path)
        for file_hash, dupes in by_hash.items():
            if len(dupes) > 1:
                groups.append((size, file_hash, sorted(dupes)))

    # Biggest wins first: sort by reclaimable bytes = (n-1) * size.
    groups.sort(key=lambda g: (len(g[2]) - 1) * g[0], reverse=True)
    return groups


def run_dupes(folder: Path, delete: bool) -> int:
    """Scan for duplicates, print the report, optionally delete."""
    if not folder.is_dir():
        print(f"Error: '{folder}' is not a folder.", file=sys.stderr)
        return 1

    print(f"Scanning {folder} ...")
    groups = find_duplicates(folder)

    if not groups:
        print("No duplicates found. Your disk thanks you.")
        return 0

    total_wasted = sum((len(paths) - 1) * size for size, _, paths in groups)
    print(f"\nFound {len(groups)} duplicate group(s), "
          f"{human_size(total_wasted)} reclaimable:\n")

    for i, (size, _file_hash, paths) in enumerate(groups, start=1):
        wasted = (len(paths) - 1) * size
        print(f"[{i}] {len(paths)} copies × {human_size(size)} "
              f"each — {human_size(wasted)} wasted")
        for p in paths:
            print(f"      {p}")
        print()

    if not delete:
        print("Re-run with --delete to interactively remove duplicates.")
        return 0

    # Interactive deletion: keep the first file (alphabetical), confirm each
    # group separately, and default to NO. Nothing is ever batch-deleted.
    freed = 0
    for i, (size, _file_hash, paths) in enumerate(groups, start=1):
        keep, *remove = paths
        print(f"[{i}] Keep: {keep}")
        for p in remove:
            print(f"      delete: {p}")
        answer = input(
            f"Delete {len(remove)} duplicate(s) ({human_size((len(remove)) * size)})? [y/N] "
        ).strip().lower()
        if answer != "y":
            print("  skipped.")
            continue
        for p in remove:
            try:
                p.unlink()
                freed += size
                print(f"  deleted {p.name}")
            except OSError as e:
                print(f"  ! could not delete {p}: {e}")

    print(f"\nDone — freed {human_size(freed)}.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="desksweep",
        description="DeskSweep — organize a messy folder and find duplicate files.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    org = sub.add_parser("organize", help="sort files into type-based subfolders")
    org.add_argument("folder", nargs="?", default=str(Path.home() / "Downloads"),
                     help="folder to organize (default: ~/Downloads)")
    org.add_argument("-n", "--dry-run", action="store_true",
                     help="preview the plan without moving anything")
    org.add_argument("--undo", action="store_true",
                     help="reverse the most recent organize run for this folder")

    dup = sub.add_parser("dupes", help="find duplicate files wasting disk space")
    dup.add_argument("folder", nargs="?", default=str(Path.home() / "Downloads"),
                     help="folder to scan, recursively (default: ~/Downloads)")
    dup.add_argument("--delete", action="store_true",
                     help="interactively delete duplicates (asks per group)")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "organize":
        folder = Path(args.folder).expanduser().resolve()
        if args.undo:
            return run_undo(folder)
        return run_organize(folder, dry_run=args.dry_run)

    if args.command == "dupes":
        folder = Path(args.folder).expanduser().resolve()
        return run_dupes(folder, delete=args.delete)

    return 1  # pragma: no cover (argparse enforces a subcommand)


if __name__ == "__main__":
    sys.exit(main())
