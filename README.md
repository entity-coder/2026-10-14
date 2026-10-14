# DeskSweep

One command to clean a messy folder: **organize** loose files into type-based
subfolders, and **find duplicates** eating your disk space.

```bash
python desksweep.py organize ~/Downloads --dry-run   # preview the plan first
python desksweep.py organize ~/Downloads              # actually sort the files
python desksweep.py organize ~/Downloads --undo       # changed your mind? put them back
python desksweep.py dupes ~/Downloads                 # report duplicate groups
python desksweep.py dupes ~/Downloads --delete        # interactively delete dupes
```

Pure Python standard library — zero dependencies, runs on Python 3.8+ anywhere.

## Features

**`organize` — file sorter**
- Sorts top-level files by extension into `Images/`, `Documents/`, `Videos/`,
  `Audio/`, `Archives/`, `Code/`, and `Other/` subfolders.
- `--dry-run` previews every move before anything happens.
- Name collisions never overwrite: `photo.jpg` becomes `photo (1).jpg`.
- Every real run writes an undo log to `~/.desksweep/undo-logs/`, so
  `--undo` restores the last run for that folder.
- Never touches hidden files (`.gitignore`), system junk (`Thumbs.db`,
  `desktop.ini`), or subdirectories — only loose files get sorted.

**`dupes` — duplicate finder**
- Two-pass scan for speed: groups by file size first (one cheap `stat()` per
  file), then SHA-256-hashes only files that share a size. Big unique files
  are never hashed.
- Reports duplicate groups sorted by wasted space, with human-readable sizes
  and a total reclaimable figure.
- `--delete` is interactive: keeps the first copy, asks `y/N` per group
  (defaults to **no**), and only then deletes. Nothing is ever batch-deleted.
- Hashes stream in 1 MB chunks, so multi-GB files use constant memory.

## How to run

```bash
# No install needed — just Python 3.8+
python desksweep.py organize --dry-run        # uses ~/Downloads by default
python desksweep.py organize ~/Pictures -n
python desksweep.py dupes ~/Documents
python desksweep.py dupes ~/Documents --delete

# Run the smoke tests
python test_desksweep.py
```

Defaults to `~/Downloads` when no folder is given. `desksweep.py` is a single
file, so you can drop it anywhere on your PATH and `chmod +x` it.

## Files

| File | What it is |
|---|---|
| `desksweep.py` | The whole tool: CLI, organize, dupes, undo (stdlib only) |
| `test_desksweep.py` | 13 smoke tests (categories, organize, undo, collisions, dupes) |
| `.gitignore` | Ignores `__pycache__/` and the local undo dir |

## Interview notes

**What is DeskSweep?**
A single-file Python CLI that tidies messy folders: it sorts loose files into
type-based subfolders (`organize`, with dry-run preview and undo) and finds
duplicate files wasting disk space (`dupes`, with SHA-256 hashing and safe
interactive deletion). Everything runs on the standard library.

**Why a single file with argparse, not a package or a TUI?**
Scope. A daily portfolio project has to stay small enough to be finished and
tested in one sitting. argparse gives a real CLI with subcommands and `--help`
for free, and a single file is trivially portable — drop it on PATH and run.
A package structure (`src/`, entry points) or a TUI would add ceremony without
adding capability for two commands.

**Why two passes (size → hash) in the duplicate finder?**
Hashing is I/O- and CPU-expensive; `stat()` is nearly free. Most folders have
far more unique-size files than same-size ones, so grouping by size first
eliminates the vast majority of hashing work. Only same-size candidates get
SHA-256'd, which is the standard way tools like `fdupes`/`rdfind` do it. The
trade-off: two files with identical size but different content still cost one
hash each — unavoidable if you want content certainty rather than a guess.

**Why SHA-256 instead of MD5?**
MD5 is faster but has known collision attacks. For a tool whose whole job is
declaring "these files are identical," I'd rather pay SHA-256's modest speed
cost and never delete a file on a wrong match. Hashes stream in 1 MB chunks
so memory stays flat even on multi-GB files.

**Why is deletion interactive with a default of "no"?**
Deleting the wrong file is the one unrecoverable action in this tool, so the
safe path has to be the default path. `--delete` keeps the first (alphabetical)
copy and asks `y/N` per group — a tired user hammering Enter deletes nothing.
The trade-off is speed: cleaning hundreds of groups means hundreds of
prompts. An acceptable one for a safety-first tool; a future `--yes` batch
flag would need a trash/recycle-bin safety net first.

**Why does `organize` only touch top-level files?**
Reaching into subdirectories would destroy folder structures the user already
arranged. Sorting only loose files makes the tool predictable, makes re-runs
idempotent (already-sorted files are skipped), and keeps the undo log simple:
every move is a flat src → dst rename.

**Why rename on collision (`photo (1).jpg`) instead of overwriting or skipping?**
Overwriting destroys data; skipping silently leaves the folder messy and the
user wondering what happened. Renaming is the only option that preserves both
files and communicates what happened — the dry-run preview shows the rename
before it occurs.

**Main limitations / trade-offs**
- Categories are extension-based, not content-sniffed: a `.docx` renamed to
  `.zip` lands in Archives. Extensions are what users organize by, and byte
  sniffing can't distinguish ZIP-based formats (`.docx`, `.xlsx`, `.zip`)
  anyway.
- `organize` never merges into existing category folders recursively and
  leaves empty subfolders behind after undo.
- `dupes` compares content only — near-duplicates (resized photos, edited
  docs) are not detected; that would need perceptual hashing (phash), which
  needs third-party libraries and breaks the zero-dependency promise.
- Undo covers only the most recent organize run per folder, and only while
  the log in `~/.desksweep/undo-logs/` still exists.

**How I'd extend it**
- `--watch` mode that auto-organizes new downloads as they arrive (using
  `watchdog`, or polling with `os.scandir` to stay stdlib-only).
- Move duplicates to the OS trash (`send2trash`) instead of permanent delete,
  enabling a safe `--yes` batch mode.
- Size and age filters (`--min-size 10M`, `--older-than 90d`) for the dupes
  scan; JSON output (`--json`) for scripting.
- Optional perceptual hashing (`--similar-images`) as an opt-in extra with
  Pillow installed — kept out of the default path to preserve the zero-
  dependency core.
