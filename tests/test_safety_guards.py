# -*- coding: utf-8 -*-
"""Safety regression tests for local-only operation and archive preservation."""
import io
import os
import sqlite3
import tempfile
import unittest

import app


class SafetyGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="digital_archive_safety_")
        self.old_db_file = app.DB_FILE
        self.old_base_dir = app.BASE_DIR
        self.old_static_dir = app.STATIC_DIR
        self.old_chrome_process = app.CHROME_PROCESS
        self.old_chrome_profile_dir = getattr(app, "CHROME_PROFILE_DIR", None)
        app.BASE_DIR = self.temp_dir
        app.STATIC_DIR = os.path.join(self.temp_dir, "static")
        os.makedirs(app.STATIC_DIR, exist_ok=True)
        app.DB_FILE = os.path.join(self.temp_dir, "archive.db")

    def tearDown(self):
        app.DB_FILE = self.old_db_file
        app.BASE_DIR = self.old_base_dir
        app.STATIC_DIR = self.old_static_dir
        app.CHROME_PROCESS = self.old_chrome_process
        if hasattr(app, "CHROME_PROFILE_DIR"):
            app.CHROME_PROFILE_DIR = self.old_chrome_profile_dir
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_fresh_database_initializes_and_is_integral(self):
        app.init_db()
        self.assertTrue(os.path.isfile(app.DB_FILE))
        self.assertEqual(app.DEFAULT_SETTINGS['system_name'], 'Digital Archive Pro')
        self.assertIn('© Noureddine 2026', app.login_page())
        self.assertIn('Digital Archive Pro', app.login_page())
        con = sqlite3.connect(app.DB_FILE)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertIsNotNone(con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='records'"
        ).fetchone())
        con.close()

    def test_attachment_deletion_preserves_files_outside_its_record_folder(self):
        archive_root = os.path.join(self.temp_dir, "archive")
        record_folder = os.path.join(archive_root, "2026", "storage", "unit", "وارد", "1")
        sibling_folder = os.path.join(archive_root, "2026", "storage", "unit", "صادر", "2")
        outside_folder = os.path.join(self.temp_dir, "outside")
        os.makedirs(record_folder)
        os.makedirs(sibling_folder)
        os.makedirs(outside_folder)
        owned = os.path.join(record_folder, "owned.pdf")
        sibling = os.path.join(sibling_folder, "sibling.pdf")
        outside = os.path.join(outside_folder, "original.pdf")
        for path in (owned, sibling, outside):
            with open(path, "wb") as stream:
                stream.write(b"test-pdf-bytes")

        self.assertTrue(app.remove_managed_attachment_file(owned, record_folder, archive_root))
        self.assertFalse(os.path.exists(owned))
        self.assertFalse(app.remove_managed_attachment_file(sibling, record_folder, archive_root))
        self.assertTrue(os.path.isfile(sibling), "Another record's file must be preserved")
        self.assertFalse(app.remove_managed_attachment_file(outside, record_folder, archive_root))
        self.assertTrue(os.path.isfile(outside), "An external/original file must be preserved")

    def test_console_logging_survives_ascii_only_code_pages(self):
        stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="strict")
        app.safe_console_print("أرشيف", file=stream)
        stream.flush()
        output = stream.buffer.getvalue().decode("ascii")
        self.assertIn("\\u", output)

    def test_external_windows_drive_paths_remain_absolute(self):
        if os.name != "nt":
            self.skipTest("Windows drive-letter behavior is only testable on Windows")
        stored = app.stored_attachment_path(r"D:\\Archive\\file.pdf")
        self.assertEqual(os.path.normcase(stored), os.path.normcase(os.path.realpath(r"D:\\Archive\\file.pdf")))
        relative = app.relative_path_inside_archive(r"D:\\OldArchive\\2026\\unit\\file.pdf",
                                                    r"D:\\OldArchive")
        self.assertEqual(os.path.normcase(relative), os.path.normcase(os.path.join("2026", "unit", "file.pdf")))

    def test_incompatible_legacy_database_is_preserved_and_backed_up(self):
        con = sqlite3.connect(app.DB_FILE)
        con.execute("CREATE TABLE main_units(id INTEGER PRIMARY KEY, name TEXT)")
        con.execute("INSERT INTO main_units(name) VALUES('Legacy unit')")
        con.execute("CREATE TABLE records(id INTEGER PRIMARY KEY, legacy_number TEXT)")
        con.commit()
        con.close()

        with self.assertRaises(RuntimeError):
            app.init_db()

        self.assertTrue(os.path.isfile(app.DB_FILE), "Original database was deleted")
        backups = [name for name in os.listdir(self.temp_dir)
                   if name.startswith("archive.db.pre-migration-backup-")]
        self.assertTrue(backups, "Unique legacy safety backup was not created")
        con = sqlite3.connect(app.DB_FILE)
        self.assertEqual(con.execute("SELECT name FROM main_units").fetchone()[0], "Legacy unit")
        con.close()

    def test_browser_launcher_prefers_chrome_when_available(self):
        from unittest.mock import patch

        with patch.object(app.shutil, "which", return_value="/mock/google-chrome"), \
             patch.object(app.os.path, "isfile", return_value=True), \
             patch.object(app.subprocess, "Popen") as popen, \
             patch.object(app.webbrowser, "open") as fallback:
            self.assertTrue(app._open_chrome_or_default("http://127.0.0.1:8080"))
            popen.assert_called_once()
            self.assertIn("--new-window", popen.call_args.args[0])
            self.assertTrue(any(arg.startswith("--user-data-dir=") for arg in popen.call_args.args[0]))
            self.assertEqual(popen.call_args.args[0][-1], "http://127.0.0.1:8080")
            fallback.assert_not_called()

    def test_browser_launcher_falls_back_when_chrome_is_unavailable(self):
        from unittest.mock import patch

        with patch.object(app.shutil, "which", return_value=None), \
             patch.object(app.os.path, "isfile", return_value=False), \
             patch.object(app.webbrowser, "open", return_value=True) as fallback:
            self.assertTrue(app._open_chrome_or_default("http://127.0.0.1:8080"))
            fallback.assert_called_once_with("http://127.0.0.1:8080", new=1)

    def test_internet_shutdown_is_setting_gated_and_graceful(self):
        import inspect
        watcher = inspect.getsource(app._start_connectivity_monitor)
        self.assertIn("if cfg.get('security_internet_shutdown')=='1'", watcher)
        self.assertTrue(hasattr(app, "_request_safe_shutdown"))
        self.assertFalse(hasattr(app, "_auto_link_pdf_folder_once"))
        with open(app.__file__, "r", encoding="utf-8-sig") as source_file:
            source = source_file.read()
        self.assertNotIn("os._exit(0)", source)
        self.assertIn("server.daemon_threads=False", source)
        self.assertIn("server.block_on_close=True", source)
        self.assertIn("winsound.Beep(1150,240)", source)
        self.assertIn("--user-data-dir=", source)


if __name__ == "__main__":
    unittest.main()
