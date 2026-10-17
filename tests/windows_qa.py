# -*- coding: utf-8 -*-
"""Offline compatibility smoke test for Digital Archive Pro.

This test uses a temporary database and loopback HTTP only. It never opens the
user's archive database and never attempts to contact the internet or another
computer.
"""
import os
import sys
import time
import shutil
import struct
import platform
import tempfile
import threading
import traceback
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
REPORT = os.path.join(ROOT, "Windows_Compatibility_QA_Report.txt")
results = []

def record(name, ok, detail=""):
    state = "PASS" if ok else "FAIL"
    line = f"[{state}] {name}" + (f" — {detail}" if detail else "")
    results.append(line)
    print(line)

def main():
    temp = None
    server = None
    thread = None
    saved = {}
    try:
        record("Python import/runtime", sys.version_info >= (3, 8),
               sys.version.replace("\n", " "))
        bits = struct.calcsize("P") * 8
        record("Python architecture", bits in (32, 64), f"{bits}-bit")
        record("Operating system", True, platform.platform())
        if sys.platform == "win32" and platform.release() == "7":
            record("Windows 7 runtime target", sys.version_info[:2] == (3, 8) and bits == 32,
                   "Windows 7 requires the Python 3.8 32-bit runtime for this build profile")
        else:
            info = "[INFO] Windows 7 native test — Not verified on this machine; run this script on the actual Windows 7 32-bit computer."
            results.append(info)
            print(info)

        import app
        for key in ("BASE_DIR", "DB_FILE", "STATIC_DIR"):
            saved[key] = getattr(app, key)
        temp = tempfile.mkdtemp(prefix="digital_archive_compat_")
        app.BASE_DIR = temp
        app.DB_FILE = os.path.join(temp, "qa_archive.db")
        app.STATIC_DIR = os.path.join(ROOT, "static")
        app.init_db()
        record("Temporary database init", os.path.isfile(app.DB_FILE))
        server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.H)
        server.daemon_threads = True
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{port}/login"
        page = ""
        last_error = None
        for _ in range(30):
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    page = response.read().decode("utf-8", "replace")
                    break
            except Exception as exc:
                last_error = exc
                time.sleep(0.2)
        record("Loopback HTTP login", "Digital Archive Pro" in page and "© Noureddine 2026" in page,
               "127.0.0.1 only" if page else str(last_error))
        record("Bundled static stylesheet", os.path.isfile(os.path.join(ROOT, "static", "style.css")))
        record("No live archive DB touched", app.DB_FILE == os.path.join(temp, "qa_archive.db"))
    except Exception as exc:
        record("Compatibility test execution", False, f"{exc}\n{traceback.format_exc()}")
    finally:
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
        if thread is not None:
            thread.join(timeout=5)
        try:
            import app
            for key, value in saved.items():
                setattr(app, key, value)
        except Exception:
            pass
        if temp and os.path.isdir(temp):
            shutil.rmtree(temp, ignore_errors=True)
        try:
            with open(REPORT, "w", encoding="utf-8") as report:
                report.write("Digital Archive Pro — Windows Compatibility QA\n")
                report.write(f"Run: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                report.write("\n".join(results) + "\n")
        except Exception as exc:
            print("Could not write report:", exc)
    failed = [line for line in results if line.startswith("[FAIL]")]
    print("\nReport:", REPORT)
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
