#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline integration/regression smoke tests for نظام الأرشيف الرقمي.
Uses only Python standard library. No real archive.db is touched.
"""
import io, os, re, shutil, sqlite3, tempfile, threading, urllib.parse, urllib.request, http.cookiejar, zipfile, sys, html

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

ROOT = tempfile.mkdtemp(prefix="digital_archive_qa_")
app.BASE_DIR = ROOT
app.DB_FILE = os.path.join(ROOT, "archive.db")
app.STATIC_DIR = os.path.join(ROOT, "static")
os.makedirs(app.STATIC_DIR, exist_ok=True)
with open(os.path.join(app.STATIC_DIR, "style.css"), "w", encoding="utf-8") as f:
    f.write("body{font-family:sans-serif}")

def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)

def post_form(opener, url, data):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    return opener.open(req)


def post_multipart(opener, url, fields, filename, file_bytes, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"):
    boundary = "----DigitalArchiveQABoundary7MA4YWxkTrZu0gW"
    chunks = []
    for key, value in fields.items():
        chunks.append(("--" + boundary + "\r\n").encode("ascii"))
        chunks.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % key).encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(("--" + boundary + "\r\n").encode("ascii"))
    chunks.append(('Content-Disposition: form-data; name="file"; filename="%s"\r\n' % filename).encode("utf-8"))
    chunks.append(("Content-Type: %s\r\n\r\n" % content_type).encode("ascii"))
    chunks.append(file_bytes)
    chunks.append(b"\r\n")
    chunks.append(("--" + boundary + "--\r\n").encode("ascii"))
    body = b"".join(chunks)
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    with opener.open(req) as response:
        return response.status, response.read().decode("utf-8", "replace"), response.geturl()


def csrf(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert_true(m, "CSRF token not found")
    return m.group(1)

def get(opener, base, path):
    with opener.open(base + path) as r:
        return r.status, r.read().decode("utf-8", "replace"), r.geturl()

def post(opener, base, path, data):
    with post_form(opener, base + path, data) as r:
        return r.status, r.read().decode("utf-8", "replace"), r.geturl()

try:
    # 1) Fresh DB + schema + integrity.
    app.init_db()
    con = sqlite3.connect(app.DB_FILE)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {"users","settings","fields_config","storage_places","units","records",
                "record_links","attachments","audit_logs","quick_links","templates","sessions"}
    assert_true(required <= tables, "Missing tables: " + repr(sorted(required - tables)))
    assert_true(con.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "DB integrity failed")
    con.close()

    # 2) Core helpers: password hashing, Arabic digit normalization, natural sort, XLSX package.
    salt = b"0" * 32
    assert_true(app.hashpw("admin", salt) == app.hashpw("admin", salt), "hashpw unstable")
    assert_true(app.hashpw("admin", salt) != app.hashpw("wrong", salt), "password hash collision")
    assert_true(app.normalize_digits("١٢٣/٢٠٢٦") == "123/2026", "Arabic digit normalization failed")
    nums = sorted(["1","2","9","10","11"], key=app.natural_key)
    assert_true(nums == ["1","2","9","10","11"], "natural numeric sorting failed")
    xlsx = app.build_xlsx(["م","رقم الوارد","الموضوع"],
                           [[1,"9","أ"],[2,"10","ب"],[3,"11","ج"]])
    assert_true(xlsx[:2] == b"PK" and len(xlsx) > 500, "XLSX generation failed")
    z = zipfile.ZipFile(io.BytesIO(xlsx))
    sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert_true("م" in sheet and "رقم الوارد" in sheet, "XLSX headers missing")
    assert_true("<v>1</v>" in sheet and "<v>3</v>" in sheet, "XLSX serial values missing")
    z.close()

    # 3) Local HTTP server, login, static file, settings/dashboard and central templates.
    server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.H)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    status, html, _ = get(opener, base, "/login")
    assert_true(status == 200 and "اسم المستخدم" in html and "Digital Archive Pro" in html,
                "login page failed")
    status, _, _ = get(opener, base, "/static/style.css")
    assert_true(status == 200, "static CSS failed")
    status, settings_force, final_url = post(opener, base, "/login", {"username":"admin","password":"admin"})
    assert_true(status == 200 and "/settings" in final_url, "admin login failed")
    # Fresh installations intentionally require the first password change.
    token = csrf(settings_force)
    _, _, pw_url = post(opener, base, "/settings/password",
                        {"csrf_token":token,"new_password":"qa-admin-2026"})
    assert_true("/settings" in pw_url, "initial password change failed")

    # The main archive root is configurable, testable and persisted independently.
    _, settings_page_html, _ = get(opener, base, "/settings")
    assert_true('href="/pdf-linker"' in settings_page_html and "ربط ملفات PDF بالمراسلات" in settings_page_html
                and "لا يعمل الربط تلقائيًا" in settings_page_html,
                "manual PDF linking button missing from Settings page")
    root_path = os.path.join(ROOT, "documents")
    token = csrf(settings_page_html)
    _, root_test_page, root_test_url = post(opener, base, "/storage/root",
                                            {"csrf_token":token,"root_path":root_path,"action":"test"})
    assert_true("success=" in root_test_url, "archive root path test failed")
    token = csrf(root_test_page)
    _, _, root_save_url = post(opener, base, "/storage/root",
                               {"csrf_token":token,"root_path":root_path,"action":"save"})
    assert_true("/settings" in root_save_url, "archive root path save failed")
    con = sqlite3.connect(app.DB_FILE)
    saved_root = con.execute("SELECT value FROM settings WHERE key='archive_root_path'").fetchone()[0]
    con.close()
    assert_true(saved_root == "documents", "portable archive root setting was not persisted")

    # Login customization preview and saved values should apply without re-login.
    _, settings_page_html, _ = get(opener, base, "/settings")
    assert_true("loginCustomizationForm" in settings_page_html and "login_preview_org" in settings_page_html,
                "login customization live preview missing")
    token = csrf(settings_page_html)
    _, _, ui_url = post(opener, base, "/settings/login-ui",
                        {"csrf_token":token,"organization_name":"QA Organization",
                         "login_title":"QA Login Title","welcome_message":"QA Welcome",
                         "login_marquee":"QA marquee","login_marquee_color":"#123456"})
    assert_true("/settings" in ui_url, "login customization save failed")
    status, login_html, _ = get(opener, base, "/login")
    assert_true(status == 200 and "QA Login Title" in login_html and "QA Welcome" in login_html,
                "saved login customization did not apply")
    assert_true("© Noureddine 2026" in login_html, "fixed copyright signature missing after customization")

    status, dash, _ = get(opener, base, "/")
    assert_true(status == 200 and "الوارد" in dash and "الصادر" in dash, "dashboard failed")

    # Templates are managed directly in Settings; no global folder picker is exposed.
    status, templates_html, _ = get(opener, base, "/templates")
    assert_true(status == 200 and "نماذج المراسلات" in templates_html,
                "templates settings section failed")
    assert_true('name="unit_id"' in templates_html and 'name="file"' in templates_html
                and 'name="folder_path"' not in templates_html,
                "template management should use only name, unit and file")
    template_dir = os.path.join(ROOT, "letter_templates")
    os.makedirs(template_dir, exist_ok=True)
    with open(os.path.join(template_dir, "نموذج QA.docx"), "wb") as tf:
        tf.write(b"QA placeholder; not opened during this test")
    assert_true(not os.path.exists(os.path.join(ROOT, "documents", "workspace")),
                "template management unexpectedly created an archive workspace")

    # 4) Structure creation through the real HTTP form.
    _, settings, _ = get(opener, base, "/settings")
    token = csrf(settings)
    _, _, _ = post(opener, base, "/structure/add",
                   {"csrf_token":token,"entity_type":"storage","name":"QA مكان التخزين"})
    _, settings, _ = get(opener, base, "/settings")
    token = csrf(settings)
    _, _, _ = post(opener, base, "/structure/add",
                   {"csrf_token":token,"entity_type":"unit","name":"QA الوحدة",
                    "storage_place_id":"1"})
    con = sqlite3.connect(app.DB_FILE)
    unit_id = con.execute("SELECT id FROM units WHERE name='QA الوحدة'").fetchone()[0]
    con.close()
    assert_true(unit_id > 0, "unit creation failed")

    # Unit-only Word templates: require a unit, block accidental overwrite, and verify backups.
    _, templates_page, _ = get(opener, base, "/templates")
    assert_true('name="unit_id"' in templates_page and 'name="replace_existing"' in templates_page,
                "unit-only template form or explicit replacement option missing")
    assert_true('name="category"' not in templates_page and 'name="year"' not in templates_page
                and 'name="storage_place_id"' not in templates_page,
                "template form still exposes category/year/storage fields")
    token = csrf(templates_page)
    template_fields = {"csrf_token":token,"unit_id":str(unit_id)}
    _, _, upload_url = post_multipart(opener, base + "/templates/upload", template_fields,
                                      "نموذج الوحدة.docx", b"template-version-1")
    assert_true("success=" in upload_url, "unit-specific template upload failed: " + upload_url)
    unit_template_dir = os.path.join(template_dir, "unit_" + str(unit_id))
    unit_template = os.path.join(unit_template_dir, "نموذج الوحدة.docx")
    assert_true(os.path.isfile(unit_template), "unit-specific template was not created")
    with open(unit_template, "rb") as stream:
        assert_true(stream.read() == b"template-version-1", "uploaded template bytes changed")
    _, templates_page, _ = get(opener, base, "/templates")
    assert_true("نموذج الوحدة" in templates_page and "QA الوحدة" in templates_page,
                "uploaded unit template was not listed")
    token = csrf(templates_page)
    _, _, duplicate_url = post_multipart(opener, base + "/templates/upload",
                                         {"csrf_token":token,"unit_id":str(unit_id)},
                                         "نموذج الوحدة.docx", b"must-not-overwrite")
    assert_true("error=" in duplicate_url, "duplicate template upload was not blocked")
    with open(unit_template, "rb") as stream:
        assert_true(stream.read() == b"template-version-1", "duplicate upload overwrote the original")
    _, templates_page, _ = get(opener, base, "/templates")
    token = csrf(templates_page)
    _, _, replace_url = post_multipart(opener, base + "/templates/upload",
                                       {"csrf_token":token,"unit_id":str(unit_id),"replace_existing":"1"},
                                       "نموذج الوحدة.docx", b"template-version-2")
    assert_true("success=" in replace_url, "explicit template replacement failed")
    with open(unit_template, "rb") as stream:
        assert_true(stream.read() == b"template-version-2", "replacement content was not saved")
    backups = [name for name in os.listdir(unit_template_dir) if name.endswith(".bak")]
    assert_true(len(backups) == 1, "replacement did not preserve a backup of the previous template")
    assert_true(os.path.isfile(os.path.join(template_dir, "نموذج QA.docx")),
                "adding/replacing a unit template damaged an unrelated legacy template")
    _, new_form, _ = get(opener, base, "/incoming/new")
    assert_true("unitTemplatesPanel" in new_form and "unitTemplates.filter" in new_form
                and "نموذج الوحدة" in new_form,
                "incoming form does not provide unit-filtered template links")
    con = sqlite3.connect(app.DB_FILE)
    con.execute("INSERT INTO templates(name,year,storage_place_id,unit_id,file_path) VALUES(?,NULL,NULL,NULL,?)",
                ("QA global legacy", os.path.relpath(os.path.join(template_dir,"نموذج QA.docx"),template_dir).replace(os.sep,"/")))
    con.commit()
    con.close()
    _, templates_page, _ = get(opener, base, "/templates")
    assert_true("QA global legacy" not in templates_page, "legacy global template should not be exposed")
    _, settings, _ = get(opener, base, "/settings")
    token = csrf(settings)
    _, _, storage_delete_url = post(opener, base, "/storage/delete",
                                    {"csrf_token":token,"id":"1"})
    assert_true("error=" in storage_delete_url, "storage with a unit was deleted")
    # 5) Real incoming/outgoing creation, automatic physical location, validation.
    _, settings, _ = get(opener, base, "/settings")
    token = csrf(settings)
    fields = {"csrf_token":token,"record_number":"9","reference":"REF-9",
              "subject":"","record_date":"2026-10-06",
              "unit_id":str(unit_id),"unit_name":"QA الوحدة","notes":"QA"}
    _, _, final_url = post(opener, base, "/incoming/new", fields)
    assert_true("/records/view?id=" in final_url, "incoming creation failed")
    incoming_id = int(re.search(r"id=(\d+)", final_url).group(1))
    con = sqlite3.connect(app.DB_FILE)
    incoming_subject = con.execute("SELECT subject FROM records WHERE id=?", (incoming_id,)).fetchone()[0]
    con.close()
    assert_true(incoming_subject == "", "an empty optional correspondence subject was not preserved")
    _, incoming_detail, _ = get(opener, base, "/records/view?id=%d" % incoming_id)
    assert_true("نموذج الوحدة" in incoming_detail and "QA global legacy" not in incoming_detail,
                "record detail exposed a global template or omitted its unit's template")

    _, settings, _ = get(opener, base, "/settings")
    token = csrf(settings)
    fields = {"csrf_token":token,"record_number":"10","reference":"REF-10",
              "subject":"اختبار صادر","record_date":"2026-10-06",
              "unit_id":str(unit_id),"unit_name":"QA الوحدة","notes":"QA"}
    _, _, final_url = post(opener, base, "/outgoing/new", fields)
    assert_true("/records/view?id=" in final_url, "outgoing creation failed")
    outgoing_id = int(re.search(r"id=(\d+)", final_url).group(1))

    con = sqlite3.connect(app.DB_FILE)
    row = con.execute("SELECT physical_location FROM records WHERE id=?", (incoming_id,)).fetchone()
    assert_true(row and row[0] == "QA مكان التخزين", "automatic physical location failed")
    con.close()
    _, settings, _ = get(opener, base, "/settings")
    token = csrf(settings)
    _, _, unit_delete_url = post(opener, base, "/units/delete",
                                 {"csrf_token":token,"id":str(unit_id)})
    assert_true("error=" in unit_delete_url, "unit with records was deleted")
    status, dashboard_html, _ = get(opener, base, "/")
    assert_true('class="stat-circle circle-total"><span>2</span>' in dashboard_html,
                "dashboard total circle is not based on real records")
    assert_true("chart-fill-incoming" in dashboard_html and "chart-fill-outgoing" in dashboard_html,
                "data-driven comparison chart missing")
    assert_true("تصفية الإحصاءات" in dashboard_html, "dashboard year/unit filters missing")
    status, filtered_dashboard, _ = get(opener, base, "/?year=2026&unit_id="+str(unit_id))
    assert_true(status == 200 and 'class="stat-circle circle-total"><span>2</span>' in filtered_dashboard,
                "dashboard year/unit filters returned incorrect counts")
    status, empty_dashboard, _ = get(opener, base, "/?year=1900")
    assert_true(status == 200 and 'class="stat-circle circle-total"><span>0</span>' in empty_dashboard,
                "dashboard zero-result filter failed")
    unit_root = os.path.join(ROOT, "documents", "2026", "QA مكان التخزين", "QA الوحدة")
    assert_true(os.path.isdir(os.path.join(unit_root, "وارد")) and
                os.path.isdir(os.path.join(unit_root, "صادر")),
                "year/place/unit incoming-outgoing folder structure missing")

    # Structure preview must not create folders; explicit confirmation creates only known missing folders.
    outgoing_dir = os.path.join(unit_root, "صادر")
    shutil.rmtree(os.path.join(outgoing_dir, str(outgoing_id)))
    os.rmdir(outgoing_dir)
    unknown_dir = os.path.join(ROOT, "documents", "مجلد_غير_معروف")
    os.makedirs(unknown_dir, exist_ok=True)
    status, structure_html, _ = get(opener, base, "/storage/structure")
    assert_true(status == 200 and "مفقود" in structure_html and "مجلد_غير_معروف" in structure_html,
                "structure preview did not report missing/unknown folders")
    assert_true(not os.path.exists(outgoing_dir), "structure preview created a folder without approval")
    token = csrf(structure_html)
    _, _, structure_url = post(opener, base, "/storage/structure/create",
                               {"csrf_token":token,"confirm_create":"1"})
    assert_true("success=" in structure_url and os.path.isdir(outgoing_dir),
                "explicit creation of missing structure folders failed")
    assert_true(os.path.isdir(unknown_dir), "unknown folder was changed during structure preparation")

    # Smart restore: preview first, then import a missing record and its numbered attachment without duplicates.
    restore_root = os.path.join(ROOT, "restore_import")
    restore_dir = os.path.join(restore_root, "2025", "QA مكان التخزين", "QA الوحدة", "وارد", "777")
    os.makedirs(restore_dir, exist_ok=True)
    for filename in ("12.pdf", "12.1.pdf"):
        with open(os.path.join(restore_dir, filename), "wb") as stream:
            stream.write(b"%PDF-1.4\nrestore fixture\n")
    con = sqlite3.connect(app.DB_FILE)
    before_records = con.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    con.close()
    preview = app._archive_import_scan(restore_root, False)
    assert_true(preview["created_records"] == 1 and preview["linked_files"] == 2,
                "smart restore preview did not identify one missing record and two PDFs: " + repr(preview))
    con = sqlite3.connect(app.DB_FILE)
    assert_true(con.execute("SELECT COUNT(*) FROM records").fetchone()[0] == before_records,
                "smart restore preview modified the database")
    con.close()
    imported = app._archive_import_scan(restore_root, True)
    assert_true(imported["created_records"] == 1 and imported["linked_files"] == 2,
                "smart restore confirmation failed: " + repr(imported))
    con = sqlite3.connect(app.DB_FILE)
    restored = con.execute("SELECT id,record_date,subject FROM records WHERE direction='incoming' AND record_number='12' AND record_year=2025 AND unit_id=?", (unit_id,)).fetchone()
    assert_true(restored and restored[1] == "" and restored[2] == "",
                "restore invented a date or subject instead of leaving them blank")
    assert_true(con.execute("SELECT COUNT(*) FROM attachments WHERE record_id=?", (restored[0],)).fetchone()[0] == 2,
                "main PDF and numbered attachment were not linked to the restored record")
    con.close()
    imported_again = app._archive_import_scan(restore_root, True)
    assert_true(imported_again["created_records"] == 0 and imported_again["linked_files"] == 0 and imported_again["already_linked"] == 2,
                "smart restore repeated existing records or links")
    _, import_page, _ = get(opener, base, "/storage/import-preview")
    assert_true("استيراد ذكي من بنية الأرشيف" in import_page and "confirm_import" in import_page,
                "smart restore preview/explicit confirmation UI missing")

    # Duplicate outgoing in same year must be rejected.
    _, settings, _ = get(opener, base, "/settings")
    token = csrf(settings)
    _, _, dup_url = post(opener, base, "/outgoing/new",
                         {"csrf_token":token,"record_number":"10","reference":"DUP",
                          "subject":"مكرر","record_date":"2026-11-01",
                          "unit_id":str(unit_id),"unit_name":"QA الوحدة","notes":""})
    assert_true("error=" in dup_url, "duplicate outgoing was accepted")

    # 6) Linking/unlinking through the real route.
    _, view, _ = get(opener, base, "/records/view?id=%d" % incoming_id)
    token = csrf(view)
    _, _, _ = post(opener, base, "/records/link",
                   {"csrf_token":token,"incoming_id":str(incoming_id),
                    "outgoing_id":str(outgoing_id)})
    con = sqlite3.connect(app.DB_FILE)
    assert_true(con.execute("SELECT COUNT(*) FROM record_links").fetchone()[0] == 1,
                "link creation failed")
    link_id = con.execute("SELECT id FROM record_links").fetchone()[0]
    con.close()

    _, view, _ = get(opener, base, "/records/view?id=%d" % incoming_id)
    assert_true("السجلات المرتبطة" in view and "الصادر رقم 10/2026" in view,
                "linked record not displayed")

    token = csrf(view)
    _, _, _ = post(opener, base, "/records/unlink",
                   {"csrf_token":token,"link_id":str(link_id),
                    "incoming_id":str(incoming_id),"return_id":str(incoming_id)})
    con = sqlite3.connect(app.DB_FILE)
    assert_true(con.execute("SELECT COUNT(*) FROM record_links").fetchone()[0] == 0,
                "unlink failed")
    con.close()

    # 7) Attachments: allowed PDF, then deletion; invalid extension must not enter DB.
    pdf = b"%PDF-1.4\nQA\n"
    boundary = "----QAFormBoundary123"
    def multipart(fields, filename, content, mime):
        parts=[]
        for k,v in fields.items():
            parts.append("--"+boundary+"\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"%(k,v))
        parts.append("--"+boundary+"\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\nContent-Type: %s\r\n\r\n"%(filename,mime))
        body="".join(parts).encode("utf-8")+content+b"\r\n--"+boundary.encode()+b"--\r\n"
        return body
    _, view, _ = get(opener, base, "/records/view?id=%d" % incoming_id)
    token = csrf(view)
    body = multipart({"csrf_token":token,"record_id":str(incoming_id)}, "qa.pdf", pdf, "application/pdf")
    req = urllib.request.Request(base+"/attachments/add", data=body, method="POST",
                                 headers={"Content-Type":"multipart/form-data; boundary="+boundary})
    with opener.open(req) as r: final_url = r.geturl()
    assert_true("records/view?id=%d" % incoming_id in final_url, "attachment upload failed")
    con = sqlite3.connect(app.DB_FILE)
    aid = con.execute("SELECT id FROM attachments WHERE record_id=?", (incoming_id,)).fetchone()[0]
    rel = con.execute("SELECT relative_path FROM attachments WHERE id=?", (aid,)).fetchone()[0]
    con.close()
    assert_true(os.path.isfile(os.path.join(app.BASE_DIR, rel)), "attachment file missing")
    normalized_rel = rel.replace("\\", "/")
    assert_true("documents/2026/QA مكان التخزين/QA الوحدة/وارد/" in normalized_rel,
                "attachment was not stored in the year/place/unit/direction hierarchy")

    # Changing a populated root must preview and verify the move before updating paths.
    new_root = os.path.join(ROOT, "documents_migrated")
    _, settings_for_migration, _ = get(opener, base, "/settings")
    token = csrf(settings_for_migration)
    status, migration_preview, migration_preview_url = post(
        opener, base, "/storage/root",
        {"csrf_token":token,"root_path":new_root,"action":"save"})
    assert_true(status == 200 and "migrate-preview" in migration_preview_url and
                "سيُنقل بعد التأكيد" in migration_preview,
                "populated archive root change did not require a migration preview")
    con = sqlite3.connect(app.DB_FILE)
    root_before_confirmation = con.execute("SELECT value FROM settings WHERE key='archive_root_path'").fetchone()[0]
    con.close()
    assert_true(root_before_confirmation == "documents" and os.path.isfile(os.path.join(app.BASE_DIR, rel)),
                "migration preview changed the root or source file before confirmation")
    token = csrf(migration_preview)
    _, _, migration_url = post(opener, base, "/storage/migrate-attachments",
                               {"csrf_token":token,"new_root":new_root,"confirm_migrate":"1"})
    assert_true("success=" in migration_url, "verified attachment migration failed")
    con = sqlite3.connect(app.DB_FILE)
    migrated_root = con.execute("SELECT value FROM settings WHERE key='archive_root_path'").fetchone()[0]
    migrated_rel = con.execute("SELECT relative_path FROM attachments WHERE id=?", (aid,)).fetchone()[0]
    con.close()
    assert_true(migrated_root == "documents_migrated", "archive root did not update after migration")
    assert_true("documents_migrated/2026/QA مكان التخزين/QA الوحدة/وارد/" in migrated_rel.replace("\\", "/"),
                "attachment database path did not move to the new hierarchy")
    assert_true(os.path.isfile(os.path.join(app.BASE_DIR, migrated_rel)), "migrated attachment file missing")
    _, incoming_page, _ = get(opener, base, "/incoming")
    token = csrf(incoming_page)
    status, selected_print, selected_print_url = post(
        opener, base, "/print/selected-pdfs",
        [("csrf_token",token),("direction","incoming"),("record_ids",str(incoming_id))])
    assert_true(status == 200 and "qa.pdf" in selected_print and "printAll()" in selected_print,
                "selected PDF print page failed to include the chosen archive PDF")
    _, _, empty_selection_url = post(
        opener, base, "/print/selected-pdfs",
        [("csrf_token",token),("direction","incoming")])
    assert_true("/incoming?error=" in empty_selection_url,
                "selected-PDF validation error did not return to the incoming list")
    _, _, direct_selected_print_url = get(opener, base, "/print/selected-pdfs")
    assert_true("/reports?error=" in direct_selected_print_url,
                "direct GET to selected-PDF endpoint fell through to generic archive printing")
    unit_root = os.path.join(ROOT, "documents_migrated", "2026", "QA مكان التخزين", "QA الوحدة")

    # Backup includes the actual attachment; restore must remap paths to a different local root.
    with opener.open(base + "/backup") as response:
        backup_blob = response.read()
    with zipfile.ZipFile(io.BytesIO(backup_blob), "r") as archive:
        archive_names = archive.namelist()
        assert_true("archive.db" in archive_names and "archive_files/" in archive_names,
                    "backup does not contain the database and archive root")
        assert_true(any(name.lower().endswith(".pdf") for name in archive_names),
                    "backup did not include the attached PDF")
    con = sqlite3.connect(app.DB_FILE)
    con.execute("UPDATE settings SET value='documents_restored' WHERE key='archive_root_path'")
    con.commit()
    con.close()
    shutil.rmtree(os.path.join(ROOT, "documents_migrated"), ignore_errors=True)
    _, settings_for_restore, _ = get(opener, base, "/settings")
    token = csrf(settings_for_restore)
    restore_body = multipart({"csrf_token":token}, "qa-backup.zip", backup_blob, "application/zip")
    restore_req = urllib.request.Request(base + "/restore", data=restore_body, method="POST",
                                         headers={"Content-Type":"multipart/form-data; boundary="+boundary})
    with opener.open(restore_req) as response:
        restored_url = response.geturl()
    assert_true("restored=1" in restored_url, "archive restore failed")
    status, restored_dashboard, _ = get(opener, base, "/")
    assert_true(status == 200 and "إحصاءات الأرشيف" in restored_dashboard,
                "dashboard failed after restore")
    con = sqlite3.connect(app.DB_FILE)
    restored_root_setting = con.execute("SELECT value FROM settings WHERE key='archive_root_path'").fetchone()[0]
    restored_rel = con.execute("SELECT relative_path FROM attachments WHERE id=?", (aid,)).fetchone()[0]
    con.close()
    assert_true(restored_root_setting == "documents_restored", "restore did not preserve the current machine's archive root")
    assert_true("documents_restored/2026/QA مكان التخزين/QA الوحدة/وارد/" in restored_rel.replace("\\", "/"),
                "restore did not remap attachment paths to the current archive root")
    assert_true(os.path.isfile(os.path.join(app.BASE_DIR, restored_rel)), "restored attachment file missing")

    # Delete the restored attachment only after its path and bytes were verified.
    _, view, _ = get(opener, base, "/records/view?id=%d" % incoming_id)
    token = csrf(view)
    _, _, _ = post(opener, base, "/attachments/delete",
                   {"csrf_token":token,"id":str(aid),"record_id":str(incoming_id)})
    con = sqlite3.connect(app.DB_FILE)
    assert_true(con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 0,
                "attachment delete failed")
    con.close()

    # 8) Manual filename-only PDF linker: review first, explicit confirmation, safe unlink.
    pdf_root = os.path.join(ROOT, "external_pdfs")
    os.makedirs(pdf_root, exist_ok=True)
    pdf_samples = ["9.pdf", "10.pdf", "10.1.pdf", "100.pdf"]
    for name in pdf_samples:
        with open(os.path.join(pdf_root, name), "wb") as stream:
            stream.write(b"%PDF-1.4\nQA fixture\n")
    _, linker_page, _ = get(opener, base, "/pdf-linker")
    assert_true("ربط ملفات PDF" in linker_page and "لا يقرأ محتوى PDF" in linker_page,
                "PDF linker page missing or incorrectly describes filename-only matching")
    assert_true("auto_link_enabled" not in linker_page and "لا يعمل الربط تلقائيًا" in linker_page,
                "PDF linker must not expose automatic linking")
    token = csrf(linker_page)
    status, review_page, review_url = post(opener, base, "/pdf-linker/match",
                                           {"csrf_token":token,"folder_path":pdf_root})
    assert_true(status == 200 and "/pdf-linker/match" in review_url and
                "تأكيد الروابط الواضحة" in review_page,
                "PDF linker review page failed")
    assert_true("10.1.pdf" in review_page and "10/2026" in review_page,
                "filename suffix attachment (10.1) did not match record 10")
    match_m = re.search(r'name="matches_json" value="([^"]*)"', review_page)
    assert_true(match_m, "review page did not preserve reviewed matches")
    reviewed_json = html.unescape(match_m.group(1))
    reviewed = __import__("json").loads(reviewed_json)
    assert_true(len(reviewed) == 3, "expected only the three clear matches; 10 must not match 100")
    token = csrf(review_page)
    _, _, confirm_url = post(opener, base, "/pdf-linker/confirm",
                             {"csrf_token":token,"folder_path":pdf_root,"matches_json":reviewed_json})
    assert_true("/pdf-linker?success=" in confirm_url, "explicit PDF link confirmation failed")
    con = sqlite3.connect(app.DB_FILE)
    pdf_links = con.execute("SELECT id,record_id,relative_path FROM attachments WHERE relative_path LIKE ?",
                            (os.path.join(pdf_root, "%"),)).fetchall()
    con.close()
    assert_true(len(pdf_links) == 3, "confirmed PDF links were not persisted")
    _, linker_page, _ = get(opener, base, "/pdf-linker")
    token = csrf(linker_page)
    for link_id, record_id, path_value in pdf_links:
        _, _, unlink_url = post(opener, base, "/attachments/unlink_safe",
                                {"csrf_token":token,"attachment_id":str(link_id)})
        assert_true("/pdf-linker?success=" in unlink_url, "safe PDF unlink route failed")
        assert_true(os.path.isfile(path_value), "safe unlink deleted the original PDF")
        token = csrf(get(opener, base, "/pdf-linker")[1])
    con = sqlite3.connect(app.DB_FILE)
    left = con.execute("SELECT COUNT(*) FROM attachments WHERE relative_path LIKE ?",
                       (os.path.join(pdf_root, "%"),)).fetchone()[0]
    con.close()
    assert_true(left == 0 and all(os.path.isfile(os.path.join(pdf_root, n)) for n in pdf_samples),
                "unlink must remove only DB associations and preserve every PDF file")

    # 9) Quick links and reports/search/print endpoints.
    _, settings, _ = get(opener, base, "/settings")
    token = csrf(settings)
    _, _, qurl = post(opener, base, "/quick-links/add",
                      {"csrf_token":token,"title":"QA رابط","path":"C:/QA","icon":"📁",
                       "color":"#123456","sort_order":"1"})
    assert_true("/quick-links" in qurl, "quick link creation failed")
    for path in ("/incoming","/outgoing","/search","/reports",
                 "/print/records?direction=incoming",
                 "/print/record?id=%d" % incoming_id):
        status, _, _ = get(opener, base, path)
        assert_true(status == 200, "endpoint failed: "+path)

    # 10) Numeric sorting at scale (1000 records) without using the real DB.
    con = sqlite3.connect(app.DB_FILE)
    rows=[]
    for i in range(1000):
        n = str(i+1)
        rows.append(("incoming","mail",n,2026,"R"+n,"bulk","2026-10-06",unit_id,
                     "QA مكان التخزين","", "2026-10-06 00:00:00"))
    con.executemany("""INSERT INTO records
        (direction,kind,record_number,record_year,reference,subject,record_date,
         unit_id,physical_location,notes,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
    con.commit()
    got = [r[0] for r in con.execute(
        "SELECT record_number FROM records WHERE direction='incoming' "
        "ORDER BY created_at DESC, id DESC").fetchall()]
    natural = sorted(got, key=app.natural_key)
    assert_true(len(got) == 1001 and natural[:3] == ["1","2","3"] and natural[-1] == "1000",
                "1000-record natural sort failed")
    assert_true(con.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
                "DB integrity failed after load test")
    con.close()

    # Reset settings must preserve the archive and its root.
    _, settings, _ = get(opener, base, "/settings")
    token = csrf(settings)
    _, _, wrong_settings_reset_url = post(opener, base, "/reset/settings",
                                         {"csrf_token":token,"admin_password":"wrong-password"})
    assert_true("error=" in wrong_settings_reset_url, "settings reset did not require the correct administrator password")
    _, settings, _ = get(opener, base, "/settings")
    token = csrf(settings)
    _, _, settings_reset_url = post(opener, base, "/reset/settings",
                                    {"csrf_token":token,"admin_password":"qa-admin-2026"})
    assert_true("success=" in settings_reset_url, "settings-only reset failed")
    con = sqlite3.connect(app.DB_FILE)
    assert_true(con.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1003,
                "settings-only reset deleted archive records")
    assert_true(con.execute("SELECT COUNT(*) FROM units").fetchone()[0] == 1,
                "settings-only reset deleted units")
    assert_true(con.execute("SELECT value FROM settings WHERE key='archive_root_path'").fetchone()[0] == "documents_restored",
                "settings-only reset changed the archive root")
    con.close()

    # Full archive reset requires the exact path plus a second explicit confirmation.
    _, settings, _ = get(opener, base, "/settings")
    token = csrf(settings)
    _, _, wrong_reset_url = post(opener, base, "/reset/archive",
                                 {"csrf_token":token,"confirm_path":os.path.join(ROOT,"documents_restored"),
                                  "confirm_reset":"1","admin_password":"wrong-password"})
    assert_true("error=" in wrong_reset_url, "full archive reset did not require the correct administrator password")
    _, settings, _ = get(opener, base, "/settings")
    token = csrf(settings)
    _, _, reset_url = post(opener, base, "/reset/archive",
                           {"csrf_token":token,"confirm_path":os.path.join(ROOT,"documents_restored"),
                            "confirm_reset":"1","admin_password":"qa-admin-2026"})
    assert_true("success=" in reset_url, "full archive reset failed")
    backup_archives = [name for name in os.listdir(ROOT) if name.startswith("documents_restored.before_reset_") and name.endswith(".zip")]
    assert_true(bool(backup_archives), "full archive reset did not retain a ZIP backup outside the active archive")
    with zipfile.ZipFile(os.path.join(ROOT, backup_archives[0]), "r") as backup_zip:
        assert_true(backup_zip.testzip() is None, "pre-reset archive ZIP backup is corrupt")
    con = sqlite3.connect(app.DB_FILE)
    assert_true(con.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0,
                "full archive reset left records behind")
    assert_true(con.execute("SELECT COUNT(*) FROM units").fetchone()[0] == 0 and
                con.execute("SELECT COUNT(*) FROM storage_places").fetchone()[0] == 0,
                "full archive reset left organizational structure behind")
    con.close()
    assert_true(not os.path.exists(os.path.join(ROOT,"documents_restored")),
                "full archive reset did not remove the active archive folder")
    assert_true(os.path.isdir(os.path.join(ROOT,"documents")) and os.path.isdir(unknown_dir),
                "full archive reset changed the old unmanaged folder or unknown directory")

    # 10) Missing DB recovery: delete QA DB, recreate it, ensure schema returns.
    server.shutdown(); server.server_close(); thread.join(timeout=3)
    os.remove(app.DB_FILE)
    app.init_db()
    con = sqlite3.connect(app.DB_FILE)
    assert_true(con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1,
                "missing DB recovery failed")
    con.close()

    print("QA PASS: fresh DB, login, HTTP routes, structure, incoming/outgoing, "
          "duplicate validation, links/unlinks, attachment lifecycle, quick links, "
          "reports/print/search, XLSX, 1000-record load, and missing-DB recovery.")
finally:
    try:
        if 'server' in globals():
            try: server.shutdown()
            except Exception: pass
            try: server.server_close()
            except Exception: pass
    except Exception:
        pass
    shutil.rmtree(ROOT, ignore_errors=True)
