"""Smoke tests for DeskSweep. Run with: python test_desksweep.py

All tests build throwaway folder trees under tempfile and never touch the
real ~/Downloads or the real ~/.desksweep undo dir (the latter is monkey-
patched to a temp dir).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import desksweep
from desksweep import (
    category_for,
    find_duplicates,
    human_size,
    plan_organize,
    run_organize,
    run_undo,
    unique_dest,
)


def make_file(path: Path, content: bytes = b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class TestCategories(unittest.TestCase):
    def test_known_extensions(self):
        self.assertEqual(category_for(Path("photo.jpg")), "Images")
        self.assertEqual(category_for(Path("report.PDF")), "Documents")  # case-insensitive
        self.assertEqual(category_for(Path("song.flac")), "Audio")
        self.assertEqual(category_for(Path("backup.tar.gz")), "Archives")  # last suffix wins
        self.assertEqual(category_for(Path("app.py")), "Code")

    def test_unknown_and_missing_extension(self):
        self.assertEqual(category_for(Path("data.xyz")), "Other")
        self.assertEqual(category_for(Path("LICENSE")), "Other")

    def test_human_size(self):
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(999), "999 B")
        self.assertEqual(human_size(1536), "1.5 KB")
        self.assertEqual(human_size(5 * 1024 * 1024), "5.0 MB")


class TestOrganize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        # Patch the undo dir so tests never touch ~/.desksweep
        self.undo_tmp = tempfile.TemporaryDirectory()
        self._patcher = mock.patch.object(desksweep, "UNDO_DIR", Path(self.undo_tmp.name))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.undo_tmp.cleanup()
        self.tmp.cleanup()

    def test_plan_and_run(self):
        make_file(self.folder / "pic.jpg")
        make_file(self.folder / "notes.txt")
        make_file(self.folder / "song.mp3")

        moves, skipped = plan_organize(self.folder)
        self.assertEqual(len(moves), 3)

        self.assertEqual(run_organize(self.folder, dry_run=True), 0)
        # Dry run moves nothing.
        self.assertTrue((self.folder / "pic.jpg").exists())

        self.assertEqual(run_organize(self.folder, dry_run=False), 0)
        self.assertTrue((self.folder / "Images" / "pic.jpg").exists())
        self.assertTrue((self.folder / "Documents" / "notes.txt").exists())
        self.assertTrue((self.folder / "Audio" / "song.mp3").exists())
        self.assertFalse((self.folder / "pic.jpg").exists())

        # Undo log was written to the patched dir.
        logs = list(Path(self.undo_tmp.name).glob("undo-*.json"))
        self.assertEqual(len(logs), 1)

        # Re-running is a no-op (files already in category folders).
        moves, _ = plan_organize(self.folder)
        self.assertEqual(moves, [])

    def test_undo_restores_files(self):
        make_file(self.folder / "a.jpg", b"aaa")
        make_file(self.folder / "b.txt", b"bbb")
        run_organize(self.folder, dry_run=False)

        self.assertEqual(run_undo(self.folder), 0)
        self.assertTrue((self.folder / "a.jpg").exists())
        self.assertTrue((self.folder / "b.txt").exists())
        self.assertEqual((self.folder / "a.jpg").read_bytes(), b"aaa")

    def test_name_collision_renames(self):
        make_file(self.folder / "Images" / "pic.jpg", b"existing")
        make_file(self.folder / "pic.jpg", b"new")
        run_organize(self.folder, dry_run=False)
        self.assertTrue((self.folder / "Images" / "pic.jpg").exists())
        self.assertTrue((self.folder / "Images" / "pic (1).jpg").exists())
        self.assertEqual((self.folder / "Images" / "pic (1).jpg").read_bytes(), b"new")

    def test_unique_dest(self):
        d = self.folder / "dest"
        d.mkdir()
        make_file(d / "f.txt")
        self.assertEqual(unique_dest(d, "f.txt").name, "f (1).txt")
        make_file(d / "f (1).txt")
        self.assertEqual(unique_dest(d, "f.txt").name, "f (2).txt")

    def test_skips_hidden_system_and_dirs(self):
        make_file(self.folder / ".hidden.jpg")
        make_file(self.folder / "Thumbs.db")
        make_file(self.folder / "desktop.ini")
        (self.folder / "sub").mkdir()
        make_file(self.folder / "sub" / "inside.txt")
        make_file(self.folder / "real.jpg")

        moves, skipped = plan_organize(self.folder)
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0][0].name, "real.jpg")
        self.assertEqual(skipped, 3)  # hidden + 2 system files (dir isn't counted)


class TestDupes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_duplicates_and_wasted_space(self):
        data = b"same content" * 1000
        make_file(self.folder / "a.bin", data)
        make_file(self.folder / "b.bin", data)
        make_file(self.folder / "sub" / "c.bin", data)
        make_file(self.folder / "unique.bin", b"totally different")

        groups = find_duplicates(self.folder)
        self.assertEqual(len(groups), 1)
        size, _h, paths = groups[0]
        self.assertEqual(len(paths), 3)
        self.assertEqual((len(paths) - 1) * size, 2 * len(data))

    def test_same_size_different_content_not_dupes(self):
        make_file(self.folder / "a.bin", b"AAAA")
        make_file(self.folder / "b.bin", b"BBBB")  # same size, different hash
        self.assertEqual(find_duplicates(self.folder), [])

    def test_empty_file_duplicates(self):
        make_file(self.folder / "e1.txt", b"")
        make_file(self.folder / "e2.txt", b"")
        groups = find_duplicates(self.folder)
        self.assertEqual(len(groups), 1)

    def test_skips_hidden_dirs_and_system_files(self):
        make_file(self.folder / ".git" / "a.bin", b"dup")
        make_file(self.folder / ".git" / "b.bin", b"dup")
        make_file(self.folder / "Thumbs.db", b"dup")
        make_file(self.folder / "other.db", b"dup")
        # None of the visible copies are true file duplicates of each other
        # except the pair that remains; hidden/system ones must be ignored.
        self.assertEqual(find_duplicates(self.folder), [])

    def test_sorted_by_wasted_space_desc(self):
        big = b"B" * 10000
        small = b"s" * 100
        make_file(self.folder / "big1.bin", big)
        make_file(self.folder / "big2.bin", big)
        make_file(self.folder / "s1.bin", small)
        make_file(self.folder / "s2.bin", small)
        groups = find_duplicates(self.folder)
        self.assertEqual(len(groups), 2)
        self.assertGreater((len(groups[0][2]) - 1) * groups[0][0],
                           (len(groups[1][2]) - 1) * groups[1][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
