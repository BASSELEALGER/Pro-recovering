DIGITAL ARCHIVE PRO — PORTABLE WINDOWS PACKAGE
Copyright: © Noureddine 2026

1) FIRST RUN
- Extract the entire ZIP to a normal writable folder (for example, Documents or a dedicated USB folder).
- Keep DigitalArchivePro.exe and the static folder together.
- Double-click DigitalArchivePro.exe. The program starts a local service and opens the default browser when ready.
- If port 8080 is already occupied, the program may choose another available local port; use the address shown in its console window if needed.
- Keep the console window open while using the program. Close it to stop the local service.

2) LOCAL DATA
- The database file archive.db is created beside the executable.
- The digital archive folder is selected from Settings; the default is documents.
- The separate letter_templates folder is used for correspondence templates.
- Keep this folder writable. Avoid running the program from a protected Program Files directory.
- Back up regularly from the program's backup page. When moving to another computer, use the program's backup/restore workflow so attachment paths can be remapped to the new local archive path.

3) NETWORK AND PRIVACY
- Internet access is not required for local archiving.
- The service is intended to listen on 127.0.0.1 (this computer only).
- The program does not scan other computers or send archive contents to an external service automatically.
- Do not expose the local service through port forwarding or change the host binding to 0.0.0.0.

4) WINDOWS 7 32-BIT
- The 32-bit package is built with a Python 3.8-compatible runtime profile for older Windows.
- A successful CI build is not proof of native compatibility with every Windows 7 computer. Native testing on Windows 7 SP1 32-bit with required system updates is still required before making a universal compatibility claim.
- If Windows reports a missing system component, do not download random DLL files from third-party sites. Use Windows Update or a trusted Microsoft installer.

5) SECURITY WARNINGS
- This executable is not digitally code-signed. Windows SmartScreen or antivirus software may show a warning.
- Do not disable antivirus protection just to run the program. Verify the package source and its checksum, and report any detection for review.
- Do not delete archive.db or the selected archive folder unless you intentionally want to remove the archive.

6) SUPPORT FILES
- PROJECT_TRACKER.md documents the development requirements and test status in the source repository.
- The © Noureddine 2026 signature is fixed in the program interface and is not an editable login-screen option.
