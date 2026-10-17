#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, html, json, hashlib, hmac, uuid, datetime, sqlite3, urllib.parse, socket, webbrowser, time, shutil, zipfile, io, re, ctypes, threading, subprocess, sys
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http import cookies

def safe_console_print(*args,sep=' ',end='\n',file=None,flush=False):
    stream=file or sys.stdout
    text=sep.join(str(arg) for arg in args)+end
    encoding=getattr(stream,'encoding',None) or 'ascii'
    try:
        stream.write(text)
    except UnicodeEncodeError:
        stream.write(text.encode(encoding,'backslashreplace').decode(encoding,'strict'))
    if flush:
        try: stream.flush()
        except Exception: pass

def configure_console_streams():
    # Arabic console messages must never crash a portable Windows build when
    # the active OEM code page cannot encode them. The browser UI remains UTF-8.
    for stream in (getattr(sys,'stdout',None),getattr(sys,'stderr',None)):
        try:
            if stream is not None and hasattr(stream,'reconfigure'):
                stream.reconfigure(errors='replace')
        except Exception:
            pass

configure_console_streams()

HOST='127.0.0.1'; PORT=8080
if getattr(sys,'frozen',False):
    BASE_DIR=os.path.dirname(os.path.abspath(sys.executable))
    RESOURCE_DIR=getattr(sys,'_MEIPASS',BASE_DIR)
else:
    BASE_DIR=os.path.dirname(os.path.abspath(__file__))
    RESOURCE_DIR=BASE_DIR
DB_FILE=os.path.join(BASE_DIR,'archive.db')
STATIC_DIR=os.path.join(RESOURCE_DIR,'static')
if not os.path.isdir(STATIC_DIR): STATIC_DIR=os.path.join(BASE_DIR,'static')

# Local-only protection: requests are accepted from localhost only. No network
# discovery, external connectivity probes, or outbound data transfer are used.
# The optional removable-drive warning checks only local Windows drive letters.
SECURITY_STATE={'new_drive_events':[], 'lan_alert_pending':False}
CONNECTIVITY_STATE={'internet_detected':False}
SECURITY_LOCK=threading.Lock()
INTERNET_WATCHER_STARTED=False
INTERNET_WATCHER_STOP=threading.Event()
CHROME_PROCESS=None
CHROME_PROFILE_DIR=None
SHUTDOWN_IN_PROGRESS=threading.Event()
SHUTDOWN_LOCK=threading.Lock()

def _close_managed_chrome():
    """Close only the Chrome process tree launched with this app's private profile."""
    global CHROME_PROCESS
    proc=CHROME_PROCESS
    CHROME_PROCESS=None
    if not proc:
        return
    try:
        if os.name=='nt' and proc.poll() is None:
            subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],
                           stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,timeout=5,check=False)
        elif proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=1.5)
            except Exception: proc.kill()
    except Exception:
        try:
            if proc.poll() is None: proc.kill()
        except Exception:
            pass

def _probe_external_connectivity(timeout=0.6):
    # Test public endpoints on common HTTPS and DNS ports. Always close failed sockets.
    targets=[('1.1.1.1',443),('8.8.8.8',443),('9.9.9.9',443),
             ('1.1.1.1',53),('8.8.8.8',53),('9.9.9.9',53)]
    for host,port in targets:
        try:
            with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect((host,port))
                return True
        except Exception:
            continue
    return False

def _probe_local_network():
    try:
        hostname=socket.gethostname()
        for addr in socket.getaddrinfo(hostname,None):
            ip=addr[4][0]
            if ip and not ip.startswith('127.') and ip!='::1' and not ip.startswith('169.254.'):
                return True
        s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        try:
            s.connect(('192.168.1.1',80))
            ip=s.getsockname()[0]
            if ip and not ip.startswith('127.'):
                return True
        except Exception:
            pass
        finally:
            s.close()
    except Exception:
        pass
    return False

_shutdown_after_security_warning = None
def _request_safe_shutdown(reason):
    """Stop accepting new HTTP requests, let active request threads finish, then exit normally."""
    if SHUTDOWN_IN_PROGRESS.is_set():
        return
    with SHUTDOWN_LOCK:
        if SHUTDOWN_IN_PROGRESS.is_set():
            return
        SHUTDOWN_IN_PROGRESS.set()
    safe_console_print('[SECURITY] Safe shutdown requested:',reason)
    try:
        audit(0,'تنبيه أمان','حماية الشبكة','',reason)
    except Exception:
        pass
    INTERNET_WATCHER_STOP.set()
    USB_WATCHER_STOP.set()
    server=SERVER_INSTANCE
    if server:
        try:
            server.shutdown()
        except Exception:
            pass
    else:
        _close_managed_chrome()

def _hard_internet_shutdown():
    _request_safe_shutdown('تم اكتشاف اتصال فعلي بالإنترنت — إيقاف آمن بعد إكمال الطلبات الجارية وإغلاق Chrome الخاص بالبرنامج.')

_shutdown_after_security_warning = _hard_internet_shutdown
def _start_connectivity_monitor():
    global INTERNET_WATCHER_STARTED
    if INTERNET_WATCHER_STARTED: return
    INTERNET_WATCHER_STARTED=True
    def watch():
        lan_was_connected=False
        while not INTERNET_WATCHER_STOP.is_set():
            try:
                cfg=settings()
                if cfg.get('security_internet_shutdown')=='1':
                    if _probe_external_connectivity():
                        _hard_internet_shutdown()

                if cfg.get('security_local_network_warning')=='1':
                    lan_now=_probe_local_network()
                    if lan_now and not lan_was_connected:
                        with SECURITY_LOCK:
                            SECURITY_STATE['lan_alert_pending']=True
                    lan_was_connected=lan_now
                else:
                    lan_was_connected=False
                    with SECURITY_LOCK:
                        SECURITY_STATE['lan_alert_pending']=False
            except Exception:
                pass
            INTERNET_WATCHER_STOP.wait(1.5)
    threading.Thread(target=watch,name='NetworkConnectivityWatcher',daemon=True).start()
BASE_REMOVABLE_DRIVES=set()
SERVER_INSTANCE=None
SINGLE_INSTANCE_LOCK_HANDLE=None
USB_WATCHER_STARTED=False
USB_WATCHER_STOP=threading.Event()

def _windows_all_mounted_drives():
    if os.name!='nt': return set()
    try:
        mask=ctypes.windll.kernel32.GetLogicalDrives()
        return {chr(65+i)+':\\' for i in range(26) if mask & (1<<i)}
    except Exception:
        return set()

def _windows_removable_drives():
    if os.name!='nt': return set()
    try:
        mask=ctypes.windll.kernel32.GetLogicalDrives(); get_type=ctypes.windll.kernel32.GetDriveTypeW
        return {chr(65+i)+':\\' for i in range(26) if (mask & (1<<i)) and get_type(chr(65+i)+':\\')==2}
    except Exception:
        return set()

def _start_usb_watcher():
    global USB_WATCHER_STARTED, BASE_REMOVABLE_DRIVES
    if USB_WATCHER_STARTED: return
    USB_WATCHER_STARTED=True
    BASE_REMOVABLE_DRIVES=_windows_removable_drives()
    with SECURITY_LOCK:
        SECURITY_STATE.setdefault('new_drive_events',[])
    def watch():
        global BASE_REMOVABLE_DRIVES
        while not USB_WATCHER_STOP.is_set():
            try:
                current=_windows_removable_drives()
                with SECURITY_LOCK:
                    previous=set(BASE_REMOVABLE_DRIVES)
                    added=sorted(current-previous)
                    if added:
                        SECURITY_STATE['new_drive_events']=added
                        SECURITY_STATE['new_usb_seen_at']=time.time()
                    BASE_REMOVABLE_DRIVES=current
            except Exception:
                pass
            USB_WATCHER_STOP.wait(1.0)
    threading.Thread(target=watch,name='USBDriveWatcher',daemon=True).start()

NATIVE_ALARM_STARTED=False
def _start_native_security_alarm():
    """Repeat a built-in Windows tone until USB/LAN warning is acknowledged."""
    global NATIVE_ALARM_STARTED
    if NATIVE_ALARM_STARTED:
        return
    NATIVE_ALARM_STARTED=True
    def alarm_worker():
        while not USB_WATCHER_STOP.is_set():
            try:
                cfg=security_settings()
                with SECURITY_LOCK:
                    usb_pending=bool(SECURITY_STATE.get('new_drive_events')) if cfg['usb_warning'] else False
                    lan_pending=bool(SECURITY_STATE.get('lan_alert_pending')) if cfg['lan_warning'] else False
                    if not cfg['usb_warning']:
                        SECURITY_STATE['new_drive_events']=[]
                    if not cfg['lan_warning']:
                        SECURITY_STATE['lan_alert_pending']=False
                if usb_pending or lan_pending:
                    if os.name=='nt':
                        try:
                            import winsound
                            winsound.Beep(1150,240)
                            winsound.Beep(620,240)
                        except Exception:
                            try: ctypes.windll.user32.MessageBeep(0x00000010)
                            except Exception: pass
                            USB_WATCHER_STOP.wait(0.15)
                    else:
                        USB_WATCHER_STOP.wait(0.45)
                else:
                    USB_WATCHER_STOP.wait(0.25)
            except Exception:
                USB_WATCHER_STOP.wait(0.5)
    threading.Thread(target=alarm_worker,name='NativeSecurityAlarm',daemon=True).start()

def security_settings():
    s=settings()
    return {'usb_warning':str(s.get('security_usb_warning','1')).strip()=='1',
            'lan_warning':str(s.get('security_local_network_warning','0')).strip()=='1',
            'internet_shutdown':str(s.get('security_internet_shutdown','0')).strip()=='1'}

def security_status():
    cfg=security_settings()
    mounted=_windows_all_mounted_drives()
    removable=_windows_removable_drives()
    with SECURITY_LOCK:
        events=[d for d in SECURITY_STATE.get('new_drive_events',[]) if d in removable]
        lan_alert=bool(SECURITY_STATE.get('lan_alert_pending',False)) if cfg['lan_warning'] else False
    return {'state':'offline','new_usb':events if cfg['usb_warning'] else [],
            'lan_detected':lan_alert,
            'drives':sorted(mounted),'removable':sorted(removable)}

DEFAULT_SETTINGS={
 'system_name':'Digital Archive Pro',
 'organization_name':'Digital Archive Pro',
 'login_title':'Digital Archive Pro',
 'welcome_message':'مرحبًا بكم في Digital Archive Pro',
 'incoming_title':'الوارد',
 'outgoing_title':'الصادر',
 'quick_title':'الفتح السريع',
 'search_placeholder':'ابحث بالرقم أو المرجع أو الموضوع أو مكان الحفظ أو الوحدة',
 'main_unit_prompt':'اختر الوحدة الرئيسية',
 'unit_prompt':'اختر الوحدة',
 'location_help':'أدخل مكان حفظ الوثيقة الورقية فعلياً.',
 'number_help':'أدخل رقم المراسلة كما هو في الوثيقة.',
 'subject_help':'أدخل موضوع المراسلة.',
 'date_help':'التاريخ المكتوب على الوثيقة نفسها، وليس تاريخ إدخالها للنظام.',
 'notes_help':'أي ملاحظات إضافية.',
 'confirm_delete':'هل أنت متأكد من الحذف؟',
 'print_header':'',
 'login_marquee':'Digital Archive Pro — مرحبًا بكم',
 'login_marquee_color':'#16a085',
 'templates_folder_path':'letter_templates',
 'archive_root_path':'documents',
 
 'security_usb_warning':'1','security_internet_shutdown':'0','security_local_network_warning':'0',
 'show_location_help':'1','show_number_help':'1','show_subject_help':'1','show_date_help':'1','show_notes_help':'1'
}

DEFAULT_FIELDS={
 'reference':'المرجع','incoming_number':'رقم الوارد','incoming_subject':'موضوع الوارد','incoming_date':'تاريخ الوارد','unit':'الوحدة','physical_location':'مكان حفظ الوارد','notes':'ملاحظات','created_at':'تاريخ الإضافة',
 'outgoing_number':'رقم الصادر','outgoing_subject':'موضوع الصادر','outgoing_date':'تاريخ الصادر','outgoing_location':'مكان حفظ الصادر','outgoing_notes':'ملاحظات','outgoing_created_at':'تاريخ الإضافة'
}

MENU=[('dashboard','📊 لوحة التحكم','/'),('incoming','📥 الوارد','/incoming'),('outgoing','📤 الصادر','/outgoing'),('search','🔎 البحث','/search'),('reports','📈 التقارير والطباعة','/reports'),('quick','⚡ الفتح السريع','/quick-links'),('settings','⚙️ الإعدادات','/settings'),('about','ℹ️ حول البرنامج','/about')]


def find_available_port(host, start=8080, end=8099):
    for port in range(start,end+1):
        s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind((host,port)); s.close(); return port
        except OSError: s.close()
    raise RuntimeError('لم يتم العثور على منفذ محلي متاح بين 8080 و8099.')

def db():
    c=sqlite3.connect(DB_FILE,timeout=5); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA busy_timeout=5000'); return c

def hashpw(p,s): return hashlib.pbkdf2_hmac('sha256',p.encode('utf-8'),s,100000)
def esc(x): return html.escape(str(x if x is not None else ''))

def normalize_digits(value):
    table=str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹','01234567890123456789')
    return str(value or '').translate(table)

def natural_key(value):
    text=normalize_digits(value).strip().lower()
    parts=re.split(r'(\d+)', text)
    return [int(x) if x.isdigit() else x for x in parts]

def xlsx_col(n):
    out=''
    while n:
        n,rem=divmod(n-1,26); out=chr(65+rem)+out
    return out

def xml_escape(value):
    return html.escape(str(value if value is not None else ''), quote=False)

def build_xlsx(headers, rows, title='تصدير الأرشيف'):
    # Excel 2007 can ignore sheetView rightToLeft on some installations.
    # Write the physical columns in reverse order so the serial column stays
    # on the far right in normal Excel display, while cells remain right-aligned.
    visual_headers=list(reversed(headers))
    visual_rows=[list(reversed(row)) for row in rows]
    def cell(col, row_num, value, style='1'):
        return f'<c r="{col}{row_num}" t="inlineStr" s="{style}"><is><t xml:space="preserve">{xml_escape(value)}</t></is></c>'
    def number_cell(col, row_num, value, style='1'):
        return f'<c r="{col}{row_num}" t="n" s="{style}"><v>{int(value)}</v></c>'
    xml_rows=[]
    xml_rows.append('<row r="1" ht="24" customHeight="1">' + ''.join(cell(xlsx_col(i+1), 1, h, '2') for i,h in enumerate(visual_headers)) + '</row>')
    for ridx,row in enumerate(visual_rows,2):
        parts=[]
        for i,v in enumerate(row):
            col=xlsx_col(i+1)
            parts.append(number_cell(col,ridx,v) if isinstance(v,int) else cell(col,ridx,v))
        xml_rows.append('<row r="%d">%s</row>' % (ridx, ''.join(parts)))
    last_col=xlsx_col(max(1,len(visual_headers)))
    cols=''.join(f'<col min="{i}" max="{i}" width="{max(14,min(42,len(str(h))*1.35+4)):.1f}" customWidth="1"/>' for i,h in enumerate(visual_headers,1))
    sheet=f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetViews><sheetView workbookViewId="0" rightToLeft="0"><pane xSplit="0" ySplit="1" topLeftCell="A2" activePane="bottomRight" state="frozen"/><selection pane="bottomRight" activeCell="A2" sqref="A2"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="20"/><cols>{cols}</cols><sheetData>{''.join(xml_rows)}</sheetData><autoFilter ref="A1:{last_col}{max(1,len(rows)+1)}"/></worksheet>'''
    workbook='''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews><workbookView/></bookViews><sheets><sheet name="الأرشيف" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    rels='''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'''
    wb_rels='''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'''
    styles='''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><name val="Arial"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="solid"><fgColor rgb="D9EAD3"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"><alignment horizontal="right" vertical="center"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="0"><alignment horizontal="right" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="1" borderId="0" applyFont="1" applyFill="1"><alignment horizontal="right" vertical="center" wrapText="1"/></xf></cellXfs></styleSheet>'''
    content='''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'''
    bio=io.BytesIO()
    with zipfile.ZipFile(bio,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml',content)
        z.writestr('_rels/.rels',rels)
        z.writestr('xl/workbook.xml',workbook)
        z.writestr('xl/_rels/workbook.xml.rels',wb_rels)
        z.writestr('xl/styles.xml',styles)
        z.writestr('xl/worksheets/sheet1.xml',sheet)
    return bio.getvalue()

QUICK_ICON_PATHS={
 'folder':'<path d="M3 6h6l2 2h10v10H3z"/>',
 'file':'<path d="M6 2h8l5 5v15H6z"/><path d="M14 2v6h6" fill="none" stroke="currentColor" stroke-width="2"/>',
 'archive':'<path d="M4 5h16v4H4zM6 9h12v11H6z"/><path d="M10 12h4" fill="none" stroke="currentColor" stroke-width="2"/>',
 'search':'<circle cx="10" cy="10" r="6" fill="none" stroke="currentColor" stroke-width="2"/><path d="M15 15l6 6" fill="none" stroke="currentColor" stroke-width="2"/>',
 'home':'<path d="M3 11l9-8 9 8v9H3z"/><path d="M9 20v-6h6v6" fill="none" stroke="currentColor" stroke-width="2"/>',
 'star':'<path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9z"/>',
 'pdf':'<path d="M5 2h10l4 4v16H5z"/><path d="M15 2v5h5" fill="none" stroke="currentColor" stroke-width="2"/><text x="7" y="17" font-size="6" fill="currentColor">PDF</text>',
 'word':'<rect x="4" y="3" width="16" height="18" rx="1" fill="none" stroke="currentColor" stroke-width="2"/><text x="7" y="16" font-size="8" fill="currentColor">W</text>',
 'excel':'<rect x="4" y="3" width="16" height="18" rx="1" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 8h8M8 12h8M8 16h8" fill="none" stroke="currentColor" stroke-width="1.5"/>',
 'settings':'<path d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z" fill="none" stroke="currentColor" stroke-width="2"/><path d="M4 12h-1m18 0h-1M12 4V3m0 18v-1M6.3 6.3l-.7-.7m13.8 13.8-.7-.7m0-12.4.7-.7M5.6 19.4l.7-.7" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
}
QUICK_ICON_LABELS={'folder':'مجلد','file':'ملف','archive':'أرشيف','search':'بحث','home':'رئيسية','star':'مميز','pdf':'PDF','word':'Word','excel':'Excel','settings':'إعدادات'}
def icon_html(value):
    v=str(value or 'file')
    if v in QUICK_ICON_PATHS:
        body=QUICK_ICON_PATHS[v]
        if body.endswith('</svg>'): body=body[:-6]
        return f'<svg class="quick-svg" viewBox="0 0 24 24" aria-hidden="true">{body}</svg>'
    return f'<span class="quick-icon-text">{esc(v)}</span>'

def ensure_templates_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS templates(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        year INTEGER,
        storage_place_id INTEGER,
        unit_id INTEGER,
        file_path TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(storage_place_id) REFERENCES storage_places(id) ON DELETE SET NULL,
        FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE CASCADE
    );""")
    try:
        cols={r[1] for r in conn.execute("PRAGMA table_info(templates)").fetchall()}
        if 'year' not in cols:
            conn.execute("ALTER TABLE templates ADD COLUMN year INTEGER")
        if 'storage_place_id' not in cols:
            conn.execute("ALTER TABLE templates ADD COLUMN storage_place_id INTEGER")
        if 'created_at' not in cols:
            conn.execute("ALTER TABLE templates ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    except Exception:
        pass

def ensure_record_links_schema(c):
    """Ensure record_links has the schema required by the current application.
    Keeps valid old links when possible and never touches the records table.
    """
    x=c.cursor()
    exists=x.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='record_links'").fetchone()
    required={"id","incoming_id","outgoing_id","created_at"}
    cols={r[1] for r in x.execute("PRAGMA table_info(record_links)")} if exists else set()
    if exists and not required.issubset(cols):
        stamp=datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        legacy=f"record_links_legacy_{stamp}"
        x.execute(f"ALTER TABLE record_links RENAME TO {legacy}")
        x.execute("CREATE TABLE record_links(id INTEGER PRIMARY KEY AUTOINCREMENT,incoming_id INTEGER NOT NULL,outgoing_id INTEGER NOT NULL,created_at DATETIME DEFAULT CURRENT_TIMESTAMP,UNIQUE(incoming_id,outgoing_id),FOREIGN KEY(incoming_id) REFERENCES records(id) ON DELETE CASCADE,FOREIGN KEY(outgoing_id) REFERENCES records(id) ON DELETE CASCADE)")
        oldcols={r[1] for r in x.execute(f"PRAGMA table_info({legacy})")}
        if {"incoming_id","outgoing_id"}.issubset(oldcols):
            try:
                x.execute(f"INSERT OR IGNORE INTO record_links(incoming_id,outgoing_id) SELECT l.incoming_id,l.outgoing_id FROM {legacy} l JOIN records ri ON ri.id=l.incoming_id AND ri.direction='incoming' JOIN records ro ON ro.id=l.outgoing_id AND ro.direction='outgoing'")
            except Exception:
                pass
    elif not exists:
        x.execute("CREATE TABLE record_links(id INTEGER PRIMARY KEY AUTOINCREMENT,incoming_id INTEGER NOT NULL,outgoing_id INTEGER NOT NULL,created_at DATETIME DEFAULT CURRENT_TIMESTAMP,UNIQUE(incoming_id,outgoing_id),FOREIGN KEY(incoming_id) REFERENCES records(id) ON DELETE CASCADE,FOREIGN KEY(outgoing_id) REFERENCES records(id) ON DELETE CASCADE)")

def ensure_audit_logs_schema(c):
    x=c.cursor()
    exists=x.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_logs'").fetchone()
    required={"id","timestamp","user_id","action","item_type","item_id","details"}
    cols={r[1] for r in x.execute("PRAGMA table_info(audit_logs)")} if exists else set()
    if exists and not required.issubset(cols):
        stamp=datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        legacy=f"audit_logs_legacy_{stamp}"
        x.execute(f"ALTER TABLE audit_logs RENAME TO {legacy}")
        x.execute("CREATE TABLE audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,user_id INTEGER,action TEXT,item_type TEXT,item_id TEXT,details TEXT)")
        oldcols={r[1] for r in x.execute(f"PRAGMA table_info({legacy})")}
        copycols=[k for k in ("timestamp","user_id","action","item_type","item_id","details") if k in oldcols]
        if copycols:
            cols_sql=','.join(copycols)
            try: x.execute(f"INSERT INTO audit_logs({cols_sql}) SELECT {cols_sql} FROM {legacy}")
            except Exception: pass
    elif not exists:
        x.execute("CREATE TABLE audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,user_id INTEGER,action TEXT,item_type TEXT,item_id TEXT,details TEXT)")

def create_database_safety_backup():
    if not os.path.isfile(DB_FILE):
        return ''
    stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path=DB_FILE+'.pre-migration-backup-'+stamp+'-'+uuid.uuid4().hex[:6]
    shutil.copy2(DB_FILE,backup_path)
    return backup_path

def init_db():
    # Never delete an existing archive database automatically. If its schema is
    # from an older release, preserve a byte-for-byte backup and stop safely
    # instead of silently replacing the user's archive with an empty database.
    if os.path.exists(DB_FILE):
        try:
            c=sqlite3.connect(DB_FILE)
            names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            cols={r[1] for r in c.execute('PRAGMA table_info(records)')}
            c.close()
            required_record_cols={'id','direction','kind','record_number','record_year','reference','subject','record_date','unit_id','physical_location','notes','created_at'}
            legacy_schema=('main_units' in names and ('units' not in names or not required_record_cols.issubset(cols)))
            incompatible_records=('records' in names and not required_record_cols.issubset(cols))
            if legacy_schema or incompatible_records:
                backup_path=create_database_safety_backup()
                raise RuntimeError(
                    'تم اكتشاف قاعدة بيانات من إصدار أقدم. لم تُحذف أو تُعدّل قاعدة البيانات الأصلية. '
                    'حُفظت نسخة احتياطية مستقلة في: '+backup_path+'؛ يلزم ترحيل آمن قبل المتابعة.'
                )
        except RuntimeError:
            raise
        except Exception as e:
            try:
                backup_path=create_database_safety_backup()
            except Exception as backup_error:
                backup_path='تعذر إنشاء النسخة الاحتياطية: '+str(backup_error)
            raise RuntimeError('تعذر التحقق من قاعدة البيانات الحالية؛ أوقف التشغيل لتجنب فقد البيانات. نسخة السلامة: '+backup_path+'. السبب: '+str(e))
    c=db(); x=c.cursor()
    x.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE NOT NULL,password_hash BLOB NOT NULL,salt BLOB NOT NULL,role TEXT NOT NULL DEFAULT 'admin',must_change_password INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS fields_config(internal_name TEXT PRIMARY KEY,display_name TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS storage_places(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE NOT NULL);
    CREATE TABLE IF NOT EXISTS units(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE NOT NULL,storage_place_id INTEGER NOT NULL,FOREIGN KEY(storage_place_id) REFERENCES storage_places(id) ON DELETE RESTRICT);
    CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY AUTOINCREMENT,direction TEXT NOT NULL CHECK(direction IN ('incoming','outgoing')),kind TEXT NOT NULL CHECK(kind IN ('mail','telegram')),record_number TEXT NOT NULL,record_year INTEGER NOT NULL,reference TEXT NOT NULL DEFAULT '',subject TEXT NOT NULL,record_date TEXT NOT NULL,unit_id INTEGER NOT NULL,physical_location TEXT NOT NULL,notes TEXT,created_at DATETIME DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE RESTRICT);
    CREATE TABLE IF NOT EXISTS sessions(session_id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,expires_at DATETIME NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS quick_links(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,path TEXT NOT NULL,icon TEXT DEFAULT '📄',color TEXT DEFAULT '#16a085',sort_order INTEGER DEFAULT 0,created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS attachments(id INTEGER PRIMARY KEY AUTOINCREMENT,record_id INTEGER NOT NULL,original_name TEXT NOT NULL,stored_name TEXT NOT NULL,relative_path TEXT NOT NULL,mime_type TEXT NOT NULL,size_bytes INTEGER NOT NULL,created_at DATETIME DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(record_id) REFERENCES records(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS templates(id INTEGER PRIMARY KEY AUTOINCREMENT,unit_id INTEGER NOT NULL,name TEXT NOT NULL,file_path TEXT NOT NULL,FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE CASCADE);
    ''')
    ensure_audit_logs_schema(c)
    ensure_record_links_schema(c)
    ensure_templates_schema(c)
    for k,v in DEFAULT_SETTINGS.items(): x.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)',(k,v))
    for k,old_value,new_value in (
        ('system_name','نظام الأرشيف الرقمي','Digital Archive Pro'),
        ('organization_name','نظام الأرشيف الرقمي','Digital Archive Pro'),
        ('login_title','نظام الأرشيف الرقمي','Digital Archive Pro'),
        ('welcome_message','مرحبًا بكم في نظام الأرشيف الرقمي','مرحبًا بكم في Digital Archive Pro'),
        ('login_marquee','نظام الأرشيف الرقمي — مرحباً بكم','Digital Archive Pro — مرحبًا بكم'),
    ):
        x.execute('UPDATE settings SET value=? WHERE key=? AND value=?',(new_value,k,old_value))
    for k,v in DEFAULT_FIELDS.items(): x.execute('INSERT OR IGNORE INTO fields_config(internal_name,display_name) VALUES(?,?)',(k,v))
    if x.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0:
        salt=os.urandom(32); x.execute('INSERT INTO users(username,password_hash,salt,role,must_change_password) VALUES(?,?,?,?,?)',('admin',hashpw('admin',salt),salt,'admin',1))
    x.execute('CREATE INDEX IF NOT EXISTS idx_records_direction ON records(direction)')
    x.execute('CREATE INDEX IF NOT EXISTS idx_records_date ON records(record_date)')
    x.execute('CREATE INDEX IF NOT EXISTS idx_records_created ON records(created_at)')
    x.execute('CREATE INDEX IF NOT EXISTS idx_records_number ON records(record_number)')
    x.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_outgoing_num_year ON records(record_number, record_year) WHERE direction='outgoing'")
    x.execute('CREATE INDEX IF NOT EXISTS idx_attachments_record ON attachments(record_id)')
    c.commit(); c.close()

def ensure_security_settings():
    c=db()
    defaults={
        "security_usb_warning":"1",
        "security_internet_shutdown":"0",
        "security_local_network_warning":"0"
    }
    for k,v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",(k,v))
    c.execute("DELETE FROM settings WHERE key='security_internet_delay'")
    c.commit(); c.close()

def settings():
    c=db(); d={r['key']:r['value'] for r in c.execute('SELECT key,value FROM settings')}; c.close()
    for k,v in DEFAULT_SETTINGS.items(): d.setdefault(k,v)
    return d

def fields():
    c=db(); d={r['internal_name']:r['display_name'] for r in c.execute('SELECT internal_name,display_name FROM fields_config')}; c.close()
    for k,v in DEFAULT_FIELDS.items(): d.setdefault(k,v)
    return d

def selected_pdf_error_target(form):
    """Return selected-PDF print errors to the relevant records list, never generic printing."""
    direction=str(form.get('direction','') or '').strip()
    return '/'+direction if direction in ('incoming','outgoing') else '/reports'


def record_number_display(r):
    n=str(r['record_number'] or '')
    if r['direction']=='outgoing' and r['record_date']:
        y=str(r['record_date'])[:4]
        return f'{n}/{y}'
    return n

def copy_text_for_record(r,f):
    incoming=r['direction']=='incoming'
    number=f['incoming_number'] if incoming else f['outgoing_number']
    subject=f['incoming_subject'] if incoming else f['outgoing_subject']
    date=f['incoming_date'] if incoming else f['outgoing_date']
    loc=f['physical_location'] if incoming else f['outgoing_location']
    lines=[('الاتجاه','الوارد' if incoming else 'الصادر'),(number,record_number_display(r)),(f['reference'],r['reference'] or '—'),(subject,r['subject']),(date,r['record_date']),(f['unit'],r['unit_name'] or r['main_name'] or '—'),(loc,r['physical_location']),(f['notes'],r['notes'] or '—')]
    return '\n'.join(str(a)+': '+str(b) for a,b in lines)

def effective_location(c, unit_id=None):
    if unit_id:
        r=c.execute('SELECT s.name FROM units u JOIN storage_places s ON s.id=u.storage_place_id WHERE u.id=?',(unit_id,)).fetchone()
        if r and (r['name'] or '').strip(): return r['name']
    return ''

def audit(uid,a,t,i,d=''):
    c=db(); c.execute('INSERT INTO audit_logs(user_id,action,item_type,item_id,details) VALUES(?,?,?,?,?)',(uid,a,t,str(i),d)); c.commit(); c.close()

def csrf_token_for_sid(sid):
    return hashlib.sha256((str(sid)+'|نظام-الأرشيف-الرقمي-CSRF-V8').encode('utf-8')).hexdigest()

def current_session_id(handler):
    try:
        c=cookies.SimpleCookie(handler.headers.get('Cookie','')); sid=c.get('session_id')
        return sid.value if sid else ''
    except Exception:
        return ''

def csrf(handler):
    sid=current_session_id(handler)
    return csrf_token_for_sid(sid) if sid else ''

def inject_csrf(content, token):
    if not token: return content
    hidden=f'<input type="hidden" name="csrf_token" value="{esc(token)}">'
    return re.sub(r'(<form\b[^>]*\bmethod=[\"\']POST[\"\'][^>]*>)', lambda m:m.group(1)+hidden, content, flags=re.I)

def pagination_html(path, q, page, total, per_page=50):
    pages=max(1,(total+per_page-1)//per_page)
    if pages<=1: return ''
    page=max(1,min(page,pages))
    base_params=[]
    for k,vals in q.items():
        if k!='page':
            for v in vals: base_params.append((k,v))
    def make_url(n): return path+'?'+urllib.parse.urlencode(base_params+[('page',str(n))])
    start=max(1,page-2); end=min(pages,page+2); items=[]
    if page>1: items.append(f'<a class="page-link" href="{esc(make_url(page-1))}">‹ السابق</a>')
    for n in range(start,end+1): items.append(f'<a class="page-link {"active" if n==page else ""}" href="{esc(make_url(n))}">{n}</a>')
    if page<pages: items.append(f'<a class="page-link" href="{esc(make_url(page+1))}">التالي ›</a>')
    items.append(f'<span class="page-info">صفحة {page} من {pages} — {total} سجل</span>')
    return '<div class="pagination">'+''.join(items)+'</div>'

security_js='''<script>(function(){
var usbShown={}, lanShown=false, pollBusy=false;
var audioCtx=null, alarmTimer=null;

function startAlarm(){
  try{
    if(!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if(audioCtx.state==='suspended') audioCtx.resume();
    stopAlarm();
    var high=true;
    function beep(){
      try{
        var osc=audioCtx.createOscillator();
        var g=audioCtx.createGain();
        osc.connect(g); g.connect(audioCtx.destination);
        osc.type='sawtooth';
        osc.frequency.setValueAtTime(high ? 920 : 540, audioCtx.currentTime);
        g.gain.setValueAtTime(0.7, audioCtx.currentTime);
        g.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.32);
        osc.start(); osc.stop(audioCtx.currentTime + 0.32);
        high = !high;
      }catch(e){}
    }
    beep();
    alarmTimer=setInterval(beep, 360);
  }catch(e){}
}

function stopAlarm(){
  if(alarmTimer){ clearInterval(alarmTimer); alarmTimer=null; }
}

function getModal(){
  var m=document.getElementById('securityModal');
  if(!m){
    m=document.createElement('div'); m.id='securityModal';
    m.style.cssText='display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:99999;align-items:center;justify-content:center';
    m.innerHTML='<div style="background:#fff;max-width:580px;width:92%;padding:30px;border-radius:16px;box-shadow:0 20px 60px rgba(0,0,0,.45);text-align:center"><div id="secIcon" style="font-size:52px;margin-bottom:10px">🚨</div><h2 id="securityTitle" style="color:#c0392b;margin-bottom:12px"></h2><p id="securityText" style="font-size:18px;line-height:1.8;margin-bottom:24px"></p><div id="securityActions" style="display:flex;justify-content:center;gap:12px;flex-wrap:wrap"></div></div>';
    document.body.appendChild(m);
  }
  return m;
}

function shutdownApp(){
  stopAlarm();
  try{
    var x=new XMLHttpRequest();
    x.open('POST','/security/shutdown',true);
    x.send(null);
  }catch(e){}
  setTimeout(function(){
    try{ window.close(); }catch(e){}
    location.href='about:blank';
  }, 400);
}

function showUsbAlert(drive){
  if(usbShown[drive]) return;
  usbShown[drive]=true;
  var m=getModal();
  startAlarm();
  document.getElementById('secIcon').textContent='💾';
  document.getElementById('securityTitle').textContent='إنذار أمني — وحدة تخزين جديدة (USB)';
  document.getElementById('securityText').innerHTML='تم اكتشاف إدخال وحدة تخزين جديدة: <b dir="ltr">'+drive+'</b>.<br>لحماية خصوصية وسرية وثائق الأرشيف، يمكنك متابعة العمل بحذر أو إغلاق البرنامج فوراً.';
  document.getElementById('securityActions').innerHTML='<button class="btn btn-success" id="btnUsbCont" style="padding:10px 24px;font-size:16px">متابعة العمل</button> <button class="btn btn-danger" id="btnUsbExit" style="padding:10px 24px;font-size:16px">إغلاق البرنامج</button>';
  document.getElementById('btnUsbCont').onclick=function(){
    stopAlarm();
    m.style.display='none';
    try{ var x=new XMLHttpRequest(); x.open('GET','/security/ack?drive='+encodeURIComponent(drive),true); x.send(null); }catch(e){}
  };
  document.getElementById('btnUsbExit').onclick=shutdownApp;
  m.style.display='flex';
}

function showLanAlert(){
  if(lanShown) return;
  lanShown=true;
  var m=getModal();
  startAlarm();
  document.getElementById('secIcon').textContent='🌐';
  document.getElementById('securityTitle').textContent='إنذار أمني — اتصال بالشبكة المحلية (LAN)';
  document.getElementById('securityText').innerHTML='تم اكتشاف اتصال نشط بالشبكة المحلية (LAN) أثناء تشغيل البرنامج.<br>لحماية خصوصية وسرية وثائق الأرشيف، يمكنك متابعة العمل بحذر أو إغلاق البرنامج فوراً.';
  document.getElementById('securityActions').innerHTML='<button class="btn btn-success" id="btnLanCont" style="padding:10px 24px;font-size:16px">متابعة العمل</button> <button class="btn btn-danger" id="btnLanExit" style="padding:10px 24px;font-size:16px">إغلاق البرنامج</button>';
  document.getElementById('btnLanCont').onclick=function(){
    stopAlarm();
    m.style.display='none';
    try{ var x=new XMLHttpRequest(); x.open('GET','/security/ack-lan',true); x.send(null); }catch(e){}
  };
  document.getElementById('btnLanExit').onclick=shutdownApp;
  m.style.display='flex';
}

function poll(){
  if(pollBusy) return;
  pollBusy=true;
  var x=new XMLHttpRequest();
  x.open('GET','/security/status?ts='+(new Date().getTime()),true);
  x.onreadystatechange=function(){
    if(x.readyState!==4) return;
    pollBusy=false;
    if(x.status!==200) return;
    try{
      var d=JSON.parse(x.responseText);
      if(d.new_usb && d.new_usb.length){
        for(var i=0;i<d.new_usb.length;i++){
          if(!usbShown[d.new_usb[i]]){ showUsbAlert(d.new_usb[i]); break; }
        }
      }
      if(d.lan_detected && !lanShown){
        showLanAlert();
      }
    }catch(e){}
  };
  x.onerror=function(){ pollBusy=false; };
  try{ x.send(null); }catch(e){ pollBusy=false; }
}

function initSecurity(){
  getModal();
  poll();
  setInterval(poll, 850);
  window.addEventListener('focus', poll);
}
if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', initSecurity);
else initSecurity();
})();</script>'''

def base(title,content,active='',error='',success='',uid=None):
    s=settings(); who=''
    if uid:
        c=db(); u=c.execute('SELECT username FROM users WHERE id=?',(uid,)).fetchone(); c.close(); who=esc(u['username']) if u else ''
    menu=[(k, ('⚡ '+s.get('quick_title','الفتح السريع')) if k=='quick' else label, url) for k,label,url in MENU]
    nav=''.join(f'<li><a href="{url}" class="{"active" if active==key else ""}">{label}</a></li>' for key,label,url in menu)
    net_warn = ''
    alert=net_warn+(f'<div class="alert alert-error">{esc(error)}</div>' if error else '')+(f'<div class="alert alert-success">{esc(success)}</div>' if success else '')
    if uid and active=='quick' and who: pass
    # Hide settings from non-admins; quick opening remains available to everyone.
    if uid:
        c=db(); u=c.execute('SELECT role FROM users WHERE id=?',(uid,)).fetchone(); c.close()
        if not u or u['role']!='admin': nav=''.join(f'<li><a href="{url}" class="{"active" if active==key else ""}">{label}</a></li>' for key,label,url in menu if key!='settings')
    return f'''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} - {esc(s['system_name'])}</title><link rel="stylesheet" href="/static/style.css"></head><body><div class="wrapper"><aside class="sidebar"><div class="sidebar-header"><h2>{esc(s['system_name'])}</h2></div><ul class="sidebar-menu">{nav}<li><a href="/logout">🚪 تسجيل الخروج</a></li></ul></aside><main class="main-content"><header class="top-header"><h1>{esc(title)}</h1><div class="user-info" dir="ltr">👤 {esc(s['welcome_message'])}{(' — '+who) if who else ''}</div></header>{alert}{content}</main></div><div id="securityModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:99999;align-items:center;justify-content:center"><div style="background:#fff;max-width:560px;width:92%;padding:28px;border-radius:16px;box-shadow:0 15px 50px rgba(0,0,0,.35);text-align:center"><div id="securityIcon" style="font-size:46px">⚠️</div><h2 id="securityTitle"></h2><p id="securityText" style="font-size:18px;line-height:1.8"></p><div id="securityActions"></div></div></div><footer class="app-signature" aria-label="Copyright">© Noureddine 2026</footer>{security_js}</body></html>'''

def login_page(error=''):
    s=settings(); alert=f'<div class="alert alert-error">{esc(error)}</div>' if error else ''
    marquee=esc(s.get('login_marquee',''))
    marquee_color=esc(s.get('login_marquee_color','#16a085'))
    return f'''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(s['login_title'])}</title><link rel="stylesheet" href="/static/style.css"><style>.login-brand{{font-size:38px;font-weight:900;margin:0 0 10px}}.login-sub{{font-size:18px;opacity:.75;margin-bottom:10px}}.login-welcome{{font-size:16px;line-height:1.7;margin:0 0 16px}}.login-marquee-wrap{{overflow:hidden;margin:0 -30px 22px;border-top:1px solid #e3ebe7;border-bottom:1px solid #e3ebe7;background:#f8fbfa;padding:10px 0}}.login-marquee{{display:inline-block;white-space:nowrap;color:{marquee_color};font-weight:900;font-size:20px;animation:loginMarquee 12s linear infinite;transform:translateX(-100%)}}@keyframes loginMarquee{{from{{transform:translateX(-100%)}}to{{transform:translateX(100%)}}}}</style></head><body class="login-page"><div class="login-box"><div class="login-brand">{esc(s['organization_name'])}</div><div class="login-sub">{esc(s['login_title'])} — {esc(s['system_name'])}</div><div class="login-welcome">{esc(s.get('welcome_message',''))}</div><div class="login-marquee-wrap"><div class="login-marquee">{marquee}</div></div>{alert}<form method="POST" action="/login"><div class="form-group"><label>اسم المستخدم</label><input name="username" class="form-control" required autocomplete="username"></div><div class="form-group"><label>كلمة المرور</label><input type="password" name="password" class="form-control" required autocomplete="current-password"></div><button class="btn btn-primary btn-block">تسجيل الدخول</button></form></div><div id="securityModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:99999;align-items:center;justify-content:center"><div style="background:#fff;max-width:560px;width:92%;padding:28px;border-radius:16px;box-shadow:0 15px 50px rgba(0,0,0,.35);text-align:center"><div style="font-size:46px">⚠️</div><h2 id="securityTitle"></h2><p id="securityText" style="font-size:18px;line-height:1.8"></p><div id="securityActions"></div></div></div><footer class="app-signature" aria-label="Copyright">© Noureddine 2026</footer>{security_js}</body></html>'''


def normalize_digits(text):
    trans={ord('٠'):'0',ord('١'):'1',ord('٢'):'2',ord('٣'):'3',ord('٤'):'4',ord('٥'):'5',ord('٦'):'6',ord('٧'):'7',ord('٨'):'8',ord('٩'):'9',ord('۰'):'0',ord('۱'):'1',ord('۲'):'2',ord('۳'):'3',ord('۴'):'4',ord('۵'):'5',ord('۶'):'6',ord('۷'):'7',ord('۸'):'8',ord('۹'):'9'}
    return str(text or '').translate(trans)

def number_tokens(text):
    import re
    return re.findall(r'\d+', normalize_digits(text))

def _text_tokens(value):
    return {z for z in re.findall(r'[\w\u0600-\u06ff]+', str(value or '').lower()) if len(z)>=2}

def find_link_candidates(x, record, session_id=None):
    # Only return one or two high-confidence suggestions; reference/number dominates.
    if not record: return []
    if record['direction']=='incoming':
        rows=x.execute("SELECT r.*,u.name unit_name FROM records r LEFT JOIN units u ON u.id=r.unit_id WHERE r.direction='outgoing' AND r.id<>? AND NOT EXISTS (SELECT 1 FROM record_links rl WHERE rl.incoming_id=? AND rl.outgoing_id=r.id) ORDER BY r.id DESC",(record['id'],record['id'])).fetchall()
    else:
        rows=x.execute("SELECT r.*,u.name unit_name FROM records r LEFT JOIN units u ON u.id=r.unit_id WHERE r.direction='incoming' AND r.id<>? AND NOT EXISTS (SELECT 1 FROM record_links rl WHERE rl.incoming_id=r.id AND rl.outgoing_id=?) ORDER BY r.id DESC",(record['id'],record['id'])).fetchall()
    target_num=normalize_digits(record['record_number']).strip()
    # Once a correspondence number has already been linked to this record,
    # do not keep offering another candidate with the same number. This avoids
    # repeated suggestions when old incoming records legitimately share a number.
    linked_number_rows=x.execute(
        "SELECT CASE WHEN rl.incoming_id=? THEN ro.record_number ELSE ri.record_number END AS n"
        " FROM record_links rl JOIN records ri ON ri.id=rl.incoming_id JOIN records ro ON ro.id=rl.outgoing_id"
        " WHERE rl.incoming_id=? OR rl.outgoing_id=?",
        (record['id'],record['id'],record['id'])
    ).fetchall()
    linked_numbers={normalize_digits(z['n']).strip() for z in linked_number_rows if z['n'] is not None and normalize_digits(z['n']).strip()}
    target_ref_tokens=set(number_tokens(record['reference']))
    target_subject=_text_tokens(record['subject'])
    target_unit=str(record['unit_id'] or '')
    target_year=str(record['record_date'] or '')[:4]
    scored=[]
    for rr in rows:
        rr_num=normalize_digits(rr['record_number']).strip()
        if rr_num and rr_num in linked_numbers:
            continue
        score=0; reasons=[]
        rr_ref_tokens=set(number_tokens(rr['reference']))
        if target_num and target_num in rr_ref_tokens:
            score += 78; reasons.append('رقم هذا السجل موجود في مرجع الطرف الآخر')
        if rr_num and rr_num in target_ref_tokens:
            score += 78; reasons.append('رقم الطرف الآخر موجود في مرجع هذا السجل')
        common_ref=target_ref_tokens & rr_ref_tokens
        if common_ref and not (target_num in rr_ref_tokens if target_num else False) and not (rr_num in target_ref_tokens if rr_num else False):
            score += min(55,25+10*len(common_ref)); reasons.append('أرقام مرجعية مشتركة')
        common_subject=target_subject & _text_tokens(rr['subject'])
        if len(common_subject)>=3:
            score += 10; reasons.append('تشابه قوي في الموضوع')
        elif len(common_subject)==2:
            score += 7; reasons.append('تشابه في الموضوع')
        elif len(common_subject)==1:
            score += 2
        if target_unit and target_unit==str(rr['unit_id'] or ''):
            score += 5; reasons.append('نفس الوحدة')
        if target_year and target_year==str(rr['record_date'] or '')[:4]:
            score += 3; reasons.append('نفس السنة')
        score=min(100,score)
        if score>=80:
            scored.append({'row':rr,'score':score,'reason':'، '.join(reasons)})
    scored.sort(key=lambda z:(-z['score'],-int(z['row']['id'])))
    return scored[:2]
TEMPLATE_EXTENSIONS={'.doc','.docx','.dotx'}

def resolve_templates_folder(raw_path=None):
    raw=raw_path if raw_path is not None else settings().get('templates_folder_path','letter_templates')
    raw=str(raw or '').strip() or 'letter_templates'
    path=os.path.expandvars(os.path.expanduser(raw))
    if not os.path.isabs(path):
        path=os.path.join(BASE_DIR,path)
    path=os.path.abspath(path)
    if os.name=='nt' and path.startswith(os.sep*2):
        raise ValueError('مجلدات الشبكة غير مدعومة؛ اختر مجلدًا محليًا على هذا الحاسوب.')
    return path

def resolve_archive_root(raw_path=None):
    raw=raw_path if raw_path is not None else settings().get('archive_root_path','documents')
    raw=str(raw or '').strip() or 'documents'
    path=os.path.expandvars(os.path.expanduser(raw))
    if not os.path.isabs(path):
        path=os.path.join(BASE_DIR,path)
    path=os.path.abspath(path)
    if os.name=='nt' and path.startswith(os.sep*2):
        raise ValueError('مسارات الشبكة غير مدعومة؛ اختر مسارًا محليًا على هذا الحاسوب.')
    base=os.path.realpath(BASE_DIR)
    real=os.path.realpath(path)
    if os.path.normcase(real)==os.path.normcase(base):
        raise ValueError('لا يمكن استخدام مجلد البرنامج نفسه كمخزن للأرشيف.')
    if os.path.dirname(real)==real:
        raise ValueError('لا يمكن استخدام جذر القرص كمخزن للأرشيف.')
    try:
        common=os.path.commonpath([real,base])
        if os.path.normcase(common)==os.path.normcase(real):
            raise ValueError('لا يمكن اختيار مجلد أعلى من مجلد البرنامج كمخزن للأرشيف.')
    except ValueError as e:
        if 'مجلد' in str(e) or 'جذر' in str(e) or 'مسارات' in str(e):
            raise
        # Different drives are valid for a local Windows archive path.
    return real

def safe_folder_component(value):
    value=re.sub(r'[<>:"/\\|?*]', '_', str(value or '').strip())
    value=re.sub(r'[\x00-\x1f]', '_', value)
    return value.strip(' .') or 'غير محدد'

def archive_directory_status(root,path):
    root=os.path.abspath(root)
    path=os.path.abspath(path)
    try:
        rel=os.path.relpath(path,root)
    except ValueError:
        return 'تعارض',path,[]
    if rel=='..' or rel.startswith('..'+os.sep):
        return 'تعارض',path,[]
    missing=[]
    current=root
    if os.path.exists(current) and not os.path.isdir(current):
        return 'تعارض',current,[]
    if not os.path.isdir(current):
        missing.append(current)
    if rel!='.':
        for part in rel.split(os.sep):
            current=os.path.join(current,part)
            if os.path.exists(current) and not os.path.isdir(current):
                return 'تعارض',current,missing
            if not os.path.isdir(current):
                missing.append(current)
    return ('موجود' if not missing else 'مفقود'),None,missing

def record_archive_base(record_id,root_override=None):
    c=db()
    row=c.execute('''SELECT r.record_year,u.name AS unit_name,s.name AS storage_name
                     FROM records r LEFT JOIN units u ON u.id=r.unit_id
                     LEFT JOIN storage_places s ON s.id=u.storage_place_id
                     WHERE r.id=?''',(record_id,)).fetchone()
    c.close()
    if not row:
        raise ValueError('السجل غير موجود.')
    root=resolve_archive_root(root_override) if root_override is not None else resolve_archive_root()
    return os.path.join(root,safe_folder_component(row['record_year']),
                        safe_folder_component(row['storage_name']),safe_folder_component(row['unit_name']))

def record_archive_folder(record_id, direction=None,root_override=None):
    c=db()
    row=c.execute('SELECT direction FROM records WHERE id=?',(record_id,)).fetchone()
    c.close()
    if not row:
        raise ValueError('السجل غير موجود.')
    direction=direction or row['direction']
    direction_name='وارد' if direction=='incoming' else 'صادر'
    return os.path.join(record_archive_base(record_id,root_override),direction_name,str(record_id))

def ensure_record_archive_folders(record_id,root_override=None):
    base=record_archive_base(record_id,root_override)
    os.makedirs(os.path.join(base,'وارد'),exist_ok=True)
    os.makedirs(os.path.join(base,'صادر'),exist_ok=True)
    current=record_archive_folder(record_id,root_override=root_override)
    os.makedirs(current,exist_ok=True)
    return current

def sha256_file(path):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()

def attachment_migration_plan(new_root):
    c=db()
    rows=c.execute('''SELECT a.id,a.record_id,a.relative_path,a.stored_name,a.original_name,a.size_bytes,
                             r.direction,r.record_year,r.record_number,u.name AS unit_name,sp.name AS storage_name
                      FROM attachments a JOIN records r ON r.id=a.record_id
                      LEFT JOIN units u ON u.id=r.unit_id
                      LEFT JOIN storage_places sp ON sp.id=u.storage_place_id
                      ORDER BY a.id''').fetchall()
    c.close()
    plan=[]
    for row in rows:
        item=dict(row)
        source=resolve_attachment_path(item.get('relative_path',''))
        if not item.get('unit_name') or not item.get('storage_name'):
            item.update(source=source,destination='',destination_name='',status='بيانات الوحدة أو مكان التخزين مفقودة')
            plan.append(item); continue
        destination_dir=record_archive_folder(item['record_id'],item['direction'],new_root)
        stored_name=str(item.get('stored_name') or '')
        if not stored_name or '/' in stored_name or '\\' in stored_name or os.path.basename(stored_name)!=stored_name:
            ext=os.path.splitext(str(item.get('original_name') or ''))[1].lower()
            stored_name=uuid.uuid4().hex+ext
        destination=os.path.join(destination_dir,stored_name)
        item.update(source=source,destination=destination,destination_name=stored_name)
        if not os.path.isfile(source):
            item['status']='الملف المصدر مفقود'
        elif os.path.normcase(os.path.realpath(source))==os.path.normcase(os.path.realpath(destination)):
            item['status']='في مكانه الصحيح'
        elif os.path.exists(destination):
            item['status']='تعارض: ملف الوجهة موجود'
        else:
            item['status']='سيُنقل بعد التأكيد'
        plan.append(item)
    return plan

def stored_attachment_path(full_path):
    full=os.path.realpath(full_path)
    base=os.path.realpath(BASE_DIR)
    try:
        rel=os.path.relpath(full,base)
        if rel!='..' and not rel.startswith('..'+os.sep):
            return rel
    except ValueError:
        pass
    return full

def relative_path_inside_archive(stored_path,root_setting):
    stored=str(stored_path or '')
    root_setting=str(root_setting or 'documents')
    if os.path.isabs(stored):
        root_abs=root_setting if os.path.isabs(root_setting) else os.path.join(BASE_DIR,root_setting)
        try:
            rel=os.path.relpath(os.path.normcase(stored),os.path.normcase(root_abs))
            if rel!='..' and not rel.startswith('..'+os.sep):
                return rel
        except ValueError:
            pass
        if not os.path.isabs(root_setting):
            stored_parts=stored.replace('\\\\','/').replace('\\','/').split('/')
            root_parts=root_setting.replace('\\\\','/').replace('\\','/').strip('/').split('/')
            for idx in range(len(stored_parts)-len(root_parts),-1,-1):
                if [os.path.normcase(p) for p in stored_parts[idx:idx+len(root_parts)]]==[os.path.normcase(p) for p in root_parts]:
                    tail=stored_parts[idx+len(root_parts):]
                    if tail:
                        return os.path.join(*tail)
        return None
    stored_norm=os.path.normpath(stored)
    root_norm=os.path.normpath(root_setting)
    if os.path.isabs(root_norm):
        return None
    if stored_norm==root_norm:
        return '.'
    prefix=root_norm+os.sep
    if stored_norm.startswith(prefix):
        return stored_norm[len(prefix):]
    return None

def resolve_attachment_path(stored_path):
    value=str(stored_path or '')
    if os.path.isabs(value):
        return os.path.abspath(value)
    return os.path.abspath(os.path.join(BASE_DIR,value))

def path_is_within(path, root):
    """Return True only when path is a strict descendant of root after symlink resolution."""
    real_path=os.path.realpath(os.path.abspath(path))
    real_root=os.path.realpath(os.path.abspath(root))
    try:
        return real_path!=real_root and os.path.commonpath([real_path,real_root])==real_root
    except (ValueError,OSError):
        return False


def remove_managed_attachment_file(stored_path, managed_folder, archive_root=None):
    """Delete only an attachment inside its own managed record folder; preserve external files."""
    full=os.path.realpath(resolve_attachment_path(stored_path))
    root=os.path.realpath(archive_root or resolve_archive_root())
    managed=os.path.realpath(managed_folder)
    if not path_is_within(managed,root) or not path_is_within(full,managed):
        return False
    if not os.path.isfile(full):
        return False
    os.remove(full)
    return True


def migrate_legacy_templates_to_folder(folder):
    """Copy old unit-scoped templates to the central folder without deleting originals."""
    legacy_folder=os.path.join(BASE_DIR,'templates')
    if not os.path.isdir(legacy_folder) or not os.path.isdir(folder):
        return
    try:
        c=db()
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='templates'").fetchone()
        rows=c.execute('SELECT name,file_path FROM templates ORDER BY id').fetchall() if exists else []
        c.close()
    except Exception:
        return
    for row in rows:
        source=os.path.abspath(os.path.join(legacy_folder,str(row['file_path'] or '')))
        if os.path.commonpath([source,os.path.abspath(legacy_folder)])!=os.path.abspath(legacy_folder):
            continue
        if not os.path.isfile(source):
            continue
        ext=os.path.splitext(source)[1].lower()
        if ext not in TEMPLATE_EXTENSIONS:
            continue
        base_name=os.path.basename(str(row['name'] or 'نموذج')).replace('\\','_').replace('/','_')
        base_name=re.sub(r'[<>:"/\\|?*]','_',base_name).strip(' .') or 'نموذج'
        if os.path.splitext(base_name)[1].lower() not in TEMPLATE_EXTENSIONS:
            base_name+=ext
        destination=os.path.join(folder,base_name)
        if os.path.abspath(destination)==source or os.path.exists(destination):
            continue
        try:
            shutil.copy2(source,destination)
        except Exception:
            # Preserve the original and allow the user to copy it manually.
            pass

class H(BaseHTTPRequestHandler):
    def uid(self):
        if 'Cookie' not in self.headers:return None
        c=cookies.SimpleCookie(self.headers['Cookie']); sid=c.get('session_id')
        if not sid:return None
        x=db(); r=x.execute('SELECT user_id FROM sessions WHERE session_id=? AND expires_at>?',(sid.value,datetime.datetime.now())).fetchone(); x.close(); return r['user_id'] if r else None
    def user(self):
        u=self.uid()
        if not u:return None
        c=db(); r=c.execute('SELECT * FROM users WHERE id=?',(u,)).fetchone(); c.close(); return r
    def postdata(self):
        n=int(self.headers.get('Content-Length',0)); raw=self.rfile.read(n).decode('utf8','replace')
        self._post_values=urllib.parse.parse_qs(raw,keep_blank_values=True)
        return {k:v[0].strip() for k,v in self._post_values.items()}
    def csrf_ok(self,form):
        sid=current_session_id(self); return bool(sid and str(form.get('csrf_token',''))==csrf_token_for_sid(sid))
    def require_csrf(self,form):
        if not self.csrf_ok(form): raise ValueError('رمز الحماية غير صالح أو منتهي. أعد فتح الصفحة وحاول مرة أخرى.')

    def multipart_file(self):
        ctype=self.headers.get('Content-Type','')
        m=re.search(r'boundary=([^;]+)',ctype)
        if not m: raise ValueError('صيغة رفع الملف غير صحيحة.')
        boundary=m.group(1).strip().strip('"').encode('utf-8')
        n=int(self.headers.get('Content-Length',0))
        if n>25*1024*1024: raise ValueError('حجم الملف يتجاوز 25 ميغابايت.')
        raw=self.rfile.read(n)
        parts=raw.split(b'--'+boundary)
        fields={}; upload=None
        for part in parts:
            if not part or part in (b'--',b'--\r\n'): continue
            part=part.lstrip(b'\r\n')
            if b'\r\n\r\n' not in part: continue
            head,body=part.split(b'\r\n\r\n',1); body=body.rstrip(b'\r\n')
            hs=head.decode('utf-8','replace'); nm=re.search(r'name="([^"]+)"',hs)
            if not nm: continue
            name=nm.group(1); fm=re.search(r'filename="([^"]*)"',hs)
            if fm:
                fn=os.path.basename(fm.group(1)); cm=re.search(r'Content-Type:\s*([^\r\n]+)',hs,re.I)
                upload=(name,fn,cm.group(1).strip() if cm else 'application/octet-stream',body)
            else: fields[name]=body.decode('utf-8','replace')
        return fields,upload

    def sendhtml(self,x,status=200):
        sid=current_session_id(self); x=inject_csrf(x,csrf_token_for_sid(sid) if sid else '')
        b=x.encode('utf8')
        try:
            self.send_response(status); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Cache-Control','no-store, no-cache, must-revalidate'); self.send_header('Pragma','no-cache'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return
    def redir(self,p): self.send_response(302); self.send_header('Location',p); self.end_headers()
    def require(self):
        u=self.user()
        if not u:self.redir('/login'); return None
        return u
    def admin(self,u): return u and u['role']=='admin'

    def do_GET(self):
        if self.client_address[0] not in ('127.0.0.1','::1'):
            self.send_error(403,'Localhost only')
            return
        p=urllib.parse.urlparse(self.path); path=p.path; q=urllib.parse.parse_qs(p.query)
        if path=='/security/status':
            self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
            self.wfile.write(json.dumps(security_status()).encode('utf-8')); return
        if path=='/security/ack-lan':
            with SECURITY_LOCK:
                SECURITY_STATE['lan_alert_pending']=False
            self.send_response(204); self.end_headers(); return
        if path=='/security/ack':
            drive=(q.get('drive',[''])[0] or '').strip()
            with SECURITY_LOCK:
                if drive:
                    SECURITY_STATE['new_drive_events']=[d for d in SECURITY_STATE.get('new_drive_events',[]) if d!=drive]
                else:
                    SECURITY_STATE['new_drive_events']=[]
            self.send_response(204); self.end_headers(); return
        if path in ('/templates/download','/settings/templates/download'):
            tid=q.get('id',[''])[0]
            c_tmp=db()
            row=c_tmp.execute('SELECT * FROM templates WHERE id=?',(tid,)).fetchone()
            c_tmp.close()
            if not row: self.send_error(404,'النموذج غير موجود'); return
            tfolder=resolve_templates_folder(settings().get('templates_folder_path','letter_templates'))
            target_file=os.path.realpath(os.path.join(tfolder,row['file_path']))
            if not os.path.isfile(target_file) or not path_is_within(target_file,tfolder):
                self.send_error(404,'ملف النموذج غير موجود على القرص'); return
            with open(target_file,'rb') as fp: file_data=fp.read()
            self.send_response(200)
            self.send_header('Content-Type','application/vnd.openxmlformats-officedocument.wordprocessingml.document')
            self.send_header('Content-Disposition',f'attachment; filename="{urllib.parse.quote(os.path.basename(target_file))}"')
            self.send_header('Content-Length',str(len(file_data)))
            self.end_headers()
            self.wfile.write(file_data); return
            drive=(q.get('drive',[''])[0] or '').strip()
            if drive:
                with SECURITY_LOCK:
                    SECURITY_STATE['new_drive_events']=[d for d in SECURITY_STATE.get('new_drive_events',[]) if d!=drive]
            self.send_response(204); self.end_headers(); return
        if path=='/static/style.css':
            try:
                b=open(os.path.join(STATIC_DIR,'style.css'),'rb').read(); self.send_response(200); self.send_header('Content-Type','text/css; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
            except Exception:self.send_error(404)
            return
        if path=='/login': self.sendhtml(login_page()); return
        if path=='/logout':
            c=cookies.SimpleCookie(self.headers.get('Cookie','')); sid=c.get('session_id')
            if sid:
                x=db(); x.execute('DELETE FROM sessions WHERE session_id=?',(sid.value,)); x.commit(); x.close()
            self.send_response(302); self.send_header('Location','/login'); self.send_header('Set-Cookie','session_id=; expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/'); self.end_headers(); return
        if path=='/print/selected-pdfs':
            self.redir('/reports?error='+urllib.parse.quote('اختر ملفات PDF من قائمة الوارد أو الصادر أولاً.')); return
        if path.startswith('/print/'):
            self.print_page(path,q); return
        if path=='/backup':
            self.backup_download(); return
        if path=='/attachments/open':
            self.attachment_open(q); return
        if path=='/export/excel':
            self.export_excel(q); return
        u=self.require()
        if not u:return
        if u['must_change_password'] and path not in ('/settings','/security','/settings/password','/logout','/security/status'):
            self.redir('/settings?force_password=1'); return
        uid=u['id']; f=fields(); s=settings(); err=q.get('error',[''])[0]; ok=q.get('success',[''])[0]
        if q.get('force_password',[''])[0]=='1': err='يجب تغيير كلمة المرور قبل متابعة استخدام النظام.'
        if path=='/templates': self.redir('/settings#templates'); return
        if path=='/' or path=='/dashboard': self.dashboard(uid,f,s,err,ok,q); return
        if path in ('/incoming','/outgoing'): self.records_page(path,q,uid,f,s,err,ok); return
        if path in ('/records/new','/incoming/new','/outgoing/new','/records/edit'): self.record_form(path,q,uid,f,s,err,ok); return
        if path=='/reports': self.reports_page(q,uid,f,s,err,ok); return
        if path=='/records/view': self.view_record(q,uid,f,s); return
        if path=='/search': self.search_page(q,uid,f,s,err,ok); return
        if path in ('/quick-links','/quick-links/add','/quick-links/edit'): self.quick_links_page(uid,f,s,err,ok); return
        if path=='/quick-links/pick': self.quick_links_pick(uid); return
        if path=='/about':
            about_content=f'''<div class="card"><h2>ℹ️ حول Digital Archive Pro</h2>
<p>برنامج محلي لإدارة وأرشفة المراسلات الواردة والصادرة، مع حفظ السجلات والنسخ الرقمية على المسار الذي يحدده المستخدم.</p>
<div class="info-grid"><div><strong>طريقة التشغيل</strong><p>محلي على هذا الحاسوب، عبر المتصفح الافتراضي.</p></div>
<div><strong>الاتصالات</strong><p>لا يفحص أجهزة الشبكة الأخرى ولا يرسل محتويات الأرشيف إلى خدمات خارجية تلقائيًا.</p></div>
<div><strong>مكان التخزين</strong><p><code dir="ltr">{esc(resolve_archive_root())}</code></p></div>
<div><strong>نماذج المراسلات</strong><p><code dir="ltr">{esc(resolve_templates_folder())}</code></p></div></div>
<p class="fixed-signature" dir="ltr">© Noureddine 2026</p></div>'''
            self.sendhtml(base('حول البرنامج',about_content,'about',err,ok,uid)); return
        if path=='/pdf-linker':
            if not self.admin(u):
                self.redir('/?error='+urllib.parse.quote('أداة ربط ملفات PDF متاحة للمدير فقط.')); return
            self.pdf_linker_page(uid,err,ok); return
        if path=='/storage/migrate-preview':
            if not self.admin(u):
                self.redir('/settings?error='+urllib.parse.quote('تنظيم المرفقات متاح للمدير فقط.')); return
            try:
                new_root=resolve_archive_root(q.get('new_root',[''])[0] or s.get('archive_root_path','documents'))
                parent=os.path.dirname(new_root)
                if not os.path.isdir(parent) and not os.path.isdir(new_root):
                    raise ValueError('المجلد الأب للمسار المقترح غير موجود؛ اختبر المسار واحفظه أولًا.')
                self.archive_migration_preview_page(uid,new_root,err,ok)
            except Exception as e:
                self.redir('/settings?error='+urllib.parse.quote(str(e)))
            return
        if path=='/storage/import-preview':
            if not self.admin(u):
                self.redir('/settings?error='+urllib.parse.quote('الاستيراد الذكي متاح للمدير فقط.')); return
            try:
                root=resolve_archive_root()
                year_dirs={normalize_digits(name):name for name in os.listdir(root) if re.fullmatch(r'\d{4}',normalize_digits(name)) and os.path.isdir(os.path.join(root,name))} if os.path.isdir(root) else {}
                available_years=sorted(year_dirs,reverse=True)
                requested=q.get('year',[])
                selected_years=[normalize_digits(y) for y in requested if normalize_digits(y) in available_years] if q.get('years_selected',[''])[0]=='1' else available_years
                year_choices=''.join('<label class="check-row"><input type="checkbox" name="year" value="'+esc(y)+'" '+('checked' if y in selected_years else '')+'> '+esc(y)+'</label>' for y in available_years) or '<p>لم تُكتشف مجلدات سنوات بأسماء رقمية من أربعة أرقام.</p>'
                year_ui='<form method="GET" action="/storage/import-preview"><input type="hidden" name="years_selected" value="1"><h3>اكتشاف السنوات واختيار ما تريد استيراده</h3><div class="search-box" style="flex-wrap:wrap">'+year_choices+'</div><button class="btn">معاينة السنوات المحددة</button></form>'
                report=_archive_import_scan(root,False,selected_years)
                details=''.join('<li>'+esc(v)+'</li>' for v in report['details'][:30]) or '<li>لا توجد حالات تحتاج إلى مراجعة.</li>'
                years_value=','.join(selected_years)
                content=f'''<div class="card"><h2>استيراد ذكي من بنية الأرشيف</h2>
<p>المسار: <code dir="ltr">{esc(root)}</code></p>
{year_ui}
<p>المعاينة لا تنقل أو تحذف أي ملف. تُنشأ السجلات الناقصة فقط من أسماء PDF الرقمية الخالصة، ويُستنتج الاتجاه والسنة والوحدة من المجلدات. ملف مثل 15.1.pdf مرفق بالسجل 15 ولا ينشئ سجلًا مستقلًا. لا يُقرأ محتوى PDF.</p>
<p>السنوات المحددة: <strong>{esc('، '.join(selected_years) or 'لا توجد سنوات محددة')}</strong></p>
<div class="stats-grid"><div class="stat-card"><strong>{report['created_records']}</strong><span>سجلات جديدة متوقعة</span></div><div class="stat-card"><strong>{report['linked_files']}</strong><span>ملفات قابلة للربط</span></div><div class="stat-card"><strong>{report['already_linked']}</strong><span>روابط موجودة تتجاوز</span></div><div class="stat-card"><strong>{report['conflicts']}</strong><span>تعارضات</span></div><div class="stat-card"><strong>{report['missing']}</strong><span>ملفات بلا سجل واضح</span></div></div>
<h3>الملاحظات والتعارضات</h3><ul>{details}</ul>
<p class="muted">السجلات المستوردة تترك التاريخ والموضوع فارغين بدل اختلاق بيانات؛ استكمل التاريخ من المستند الأصلي لاحقًا.</p>
<form method="POST" action="/storage/import-confirm" onsubmit="return confirm('تأكيد استيراد السنوات المحددة؟ لن تُحذف أو تُنقل ملفات.');"><input type="hidden" name="years" value="{esc(years_value)}"><label class="check-row"><input type="checkbox" name="confirm_import" value="1" required> راجعت المعاينة وأوافق على تنفيذ الاستيراد</label><button class="btn btn-success" {'disabled' if not selected_years else ''}>تأكيد الاستيراد الذكي</button> <a class="btn" href="/storage/structure">رجوع</a></form><p class="fixed-signature" dir="ltr">© Noureddine 2026</p></div>'''
                self.sendhtml(base('الاستيراد الذكي',content,'settings',err,ok,uid))
            except Exception as e:
                self.redir('/settings?error='+urllib.parse.quote(str(e)))
            return
        if path=='/storage/structure':
            if not self.admin(u):
                self.redir('/settings?error='+urllib.parse.quote('معاينة هيكل التخزين متاحة للمدير فقط.')); return
            self.archive_structure_page(uid,f,s,err,ok); return
        if path=='/storage/pick-root':
            if not self.admin(u):
                self.redir('/settings?error='+urllib.parse.quote('تغيير مسار الأرشيف متاح للمدير فقط.')); return
            try:
                import tkinter as tk
                from tkinter import filedialog
                root=tk.Tk(); root.withdraw(); root.attributes('-topmost',True)
                current=resolve_archive_root(s.get('archive_root_path','documents'))
                initial=current if os.path.isdir(current) else BASE_DIR
                picked=filedialog.askdirectory(title='اختيار مكان التخزين الرئيسي للأرشيف',initialdir=initial)
                root.destroy()
                self.redir('/settings?picked_archive_root='+urllib.parse.quote(picked) if picked else '/settings')
            except Exception as e:
                self.redir('/settings?error='+urllib.parse.quote('تعذر فتح نافذة اختيار المجلد: '+str(e)))
            return
        if path=='/templates/pick-folder':
            if not self.admin(u):
                self.redir('/templates?error='+urllib.parse.quote('تغيير مسار النماذج متاح للمدير فقط.')); return
            try:
                import tkinter as tk
                from tkinter import filedialog
                root=tk.Tk(); root.withdraw(); root.attributes('-topmost',True)
                current=resolve_templates_folder(s.get('templates_folder_path','letter_templates'))
                initial=current if os.path.isdir(current) else BASE_DIR
                picked=filedialog.askdirectory(title='اختيار مجلد نماذج المراسلات',initialdir=initial)
                root.destroy()
                self.redir('/templates?picked_path='+urllib.parse.quote(picked) if picked else '/templates')
            except Exception as e:
                self.redir('/templates?error='+urllib.parse.quote('تعذر فتح نافذة اختيار المجلد: '+str(e)))
            return
        if path=='/quick-links/open': self.quick_links_open(q,uid); return
        if path=='/security':
            if not self.admin(u): self.redir('/?error='+urllib.parse.quote('هذه الصفحة متاحة للمدير فقط.')); return
            self.security_page(uid,f,s,err,ok); return
        if path=='/settings':
            if not self.admin(u): self.redir('/?error='+urllib.parse.quote('هذه الصفحة متاحة للمدير فقط.')); return
            self.settings_page(uid,f,s,err,ok,q); return
        self.send_error(404)

    def dashboard(self,uid,f,s,err,ok,q=None):
        q=q or urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        year_filter=q.get('year',[''])[0].strip()
        unit_filter=q.get('unit_id',[''])[0].strip()
        if year_filter and not re.fullmatch(r'\d{4}',year_filter):
            year_filter=''
        if unit_filter and not unit_filter.isdigit():
            unit_filter=''
        conditions=[]; params=[]
        if year_filter:
            conditions.append('r.record_year=?'); params.append(year_filter)
        if unit_filter:
            conditions.append('r.unit_id=?'); params.append(int(unit_filter))
        record_where=(' WHERE '+' AND '.join(conditions)) if conditions else ''
        x=db()
        counts={}
        for direction in ('incoming','outgoing'):
            clause=(' AND ' if record_where else ' WHERE ')+'r.direction=?'
            counts[direction]=x.execute('SELECT COUNT(*) FROM records r'+record_where+clause,params+[direction]).fetchone()[0]
        total=counts['incoming']+counts['outgoing']
        linked=x.execute('SELECT COUNT(DISTINCT r.id) FROM records r JOIN record_links l ON (r.id=l.incoming_id OR r.id=l.outgoing_id)'+record_where,params).fetchone()[0]
        unlinked=max(0,total-linked)
        pdf_linked=x.execute('SELECT COUNT(DISTINCT r.id) FROM records r JOIN attachments a ON a.record_id=r.id'+record_where,params).fetchone()[0]
        pdf_unlinked=max(0,total-pdf_linked)
        filtered_attachments=x.execute('SELECT a.relative_path FROM attachments a JOIN records r ON r.id=a.record_id'+record_where,params).fetchall()
        broken_pdf_count=sum(1 for a in filtered_attachments if not os.path.isfile(resolve_attachment_path(a['relative_path'])))
        month=datetime.datetime.now().strftime('%Y-%m')
        month_clause=(' AND ' if record_where else ' WHERE ')+'r.direction=? AND substr(r.record_date,1,7)=?'
        month_in=x.execute('SELECT COUNT(*) FROM records r'+record_where+month_clause,params+['incoming',month]).fetchone()[0]
        month_out=x.execute('SELECT COUNT(*) FROM records r'+record_where+month_clause,params+['outgoing',month]).fetchone()[0]
        nu=x.execute('SELECT COUNT(*) FROM units').fetchone()[0]
        ns=x.execute('SELECT COUNT(*) FROM storage_places').fetchone()[0]
        rows=x.execute('''SELECT r.*,u.name unit_name FROM records r LEFT JOIN units u ON u.id=r.unit_id'''+record_where+' ORDER BY r.created_at DESC LIMIT 12',params).fetchall()
        qlinks=x.execute('SELECT * FROM quick_links ORDER BY sort_order,id').fetchall()
        year_count=x.execute('SELECT COUNT(DISTINCT r.record_year) FROM records r'+record_where,params).fetchone()[0]
        attachment_count=x.execute('SELECT COUNT(*) FROM attachments a JOIN records r ON r.id=a.record_id'+record_where,params).fetchone()[0]
        year_rows=x.execute('SELECT r.record_year,COUNT(*) AS record_count FROM records r'+record_where+' GROUP BY r.record_year ORDER BY r.record_year DESC',params).fetchall()
        if unit_filter:
            unit_join=' LEFT JOIN records r ON r.unit_id=u.id'
            unit_params=[]
            if year_filter:
                unit_join+=' AND r.record_year=?'; unit_params.append(year_filter)
            unit_rows=x.execute('SELECT u.name,COUNT(r.id) AS record_count FROM units u'+unit_join+' WHERE u.id=? GROUP BY u.id',(unit_params+[int(unit_filter)])).fetchall()
        elif year_filter:
            unit_rows=x.execute('SELECT u.name,COUNT(r.id) AS record_count FROM units u LEFT JOIN records r ON r.unit_id=u.id AND r.record_year=? GROUP BY u.id ORDER BY record_count DESC,u.name LIMIT 6',(year_filter,)).fetchall()
        else:
            unit_rows=x.execute('SELECT u.name,COUNT(r.id) AS record_count FROM units u LEFT JOIN records r ON r.unit_id=u.id GROUP BY u.id ORDER BY record_count DESC,u.name LIMIT 6').fetchall()
        years=x.execute('SELECT DISTINCT record_year FROM records ORDER BY record_year DESC').fetchall()
        units=x.execute('SELECT id,name FROM units ORDER BY name').fetchall()
        x.close()
        year_options='<option value="">كل السنوات</option>'+''.join(f'<option value="{esc(y["record_year"])}" {"selected" if str(y["record_year"])==year_filter else ""}>{esc(y["record_year"])}</option>' for y in years)
        unit_options='<option value="">كل الوحدات</option>'+''.join(f'<option value="{u["id"]}" {"selected" if str(u["id"])==unit_filter else ""}>{esc(u["name"])}</option>' for u in units)
        filter_ui=f'''<div class="card"><h2>تصفية الإحصاءات</h2><form method="GET" action="/" class="search-box">
<label>السنة <select name="year" class="form-control">{year_options}</select></label>
<label>الوحدة <select name="unit_id" class="form-control">{unit_options}</select></label>
<button class="btn btn-primary">تطبيق التصفية</button><a class="btn" href="/">إلغاء التصفية</a></form></div>'''
        incoming_pct=round(counts['incoming']*100/total) if total else 0
        outgoing_pct=round(counts['outgoing']*100/total) if total else 0
        max_year=max((int(r['record_count']) for r in year_rows),default=0)
        max_unit=max((int(r['record_count']) for r in unit_rows),default=0)
        year_bars=''.join(f'<div class="chart-row"><span>{esc(r["record_year"])}</span><div class="chart-track"><div class="chart-fill chart-fill-year" style="width:{round(int(r["record_count"])*100/max_year) if max_year else 0}%"></div></div><b>{int(r["record_count"])}</b></div>' for r in year_rows) or '<p class="muted">لا توجد بيانات سنوية بعد.</p>'
        unit_bars=''.join(f'<div class="chart-row"><span title="{esc(r["name"])}">{esc(r["name"])}</span><div class="chart-track"><div class="chart-fill chart-fill-unit" style="width:{round(int(r["record_count"])*100/max_unit) if max_unit else 0}%"></div></div><b>{int(r["record_count"])}</b></div>' for r in unit_rows) or '<p class="muted">لا توجد وحدات بعد.</p>'
        charts_html=f'''<div class="card"><h2>📊 إحصاءات الأرشيف</h2><div class="charts-grid">
<section class="chart-panel"><h3>الوارد مقابل الصادر</h3>
<div class="chart-row"><span>الوارد</span><div class="chart-track"><div class="chart-fill chart-fill-incoming" style="width:{incoming_pct}%"></div></div><b>{counts['incoming']}</b></div>
<div class="chart-row"><span>الصادر</span><div class="chart-track"><div class="chart-fill chart-fill-outgoing" style="width:{outgoing_pct}%"></div></div><b>{counts['outgoing']}</b></div>
<p class="muted">النسبة محسوبة من إجمالي {total} سجلًا وفق التصفية الحالية.</p></section>
<section class="chart-panel"><h3>التوزيع حسب السنة</h3>{year_bars}</section>
<section class="chart-panel"><h3>التوزيع حسب الوحدة</h3>{unit_bars}</section>
<section class="chart-panel"><h3>حالة الربط</h3><div class="link-summary"><div><strong>{linked}</strong><span>سجلات مرتبطة</span></div><div><strong>{unlinked}</strong><span>سجلات غير مرتبطة</span></div></div><p class="muted">الشهر الحالي: وارد {month_in} — صادر {month_out}</p></section>
<section class="chart-panel"><h3>حالة ربط ملفات PDF</h3><div class="link-summary"><div><strong>{pdf_linked}</strong><span>سجلات لها PDF</span></div><div><strong>{pdf_unlinked}</strong><span>سجلات بلا PDF</span></div><div><strong>{broken_pdf_count}</strong><span>ملفات مفقودة/روابط مكسورة</span></div></div><p class="muted">تُحسب الروابط حسب ملفات PDF المسجلة، ولا يؤدي حذف الملف الأصلي إلى حذف سجل المراسلة.</p></section>
</div></div>'''
        rh=''.join(f'<tr><td>{"وارد" if r["direction"]=="incoming" else "صادر"}</td><td>{esc(r["record_number"])}/{esc(r["record_year"])}</td><td>{esc(r["subject"])}</td><td>{esc(r["unit_name"] or "")}</td><td>{esc(effective_location(r))}</td><td><a class="btn btn-sm" href="/records/view?id={r["id"]}">عرض</a></td></tr>' for r in rows) or '<tr><td colspan="6" class="text-center">لا توجد سجلات</td></tr>'
        quick=''.join(f'<a class="btn" href="{esc(l["url"])}">{esc(l["title"])}</a>' for l in qlinks)
        c=f'''{filter_ui}<div class="stat-circles">
<div class="stat-circle-card"><div class="stat-circle circle-total"><span>{total}</span></div><h3>إجمالي المراسلات</h3></div>
<div class="stat-circle-card"><div class="stat-circle circle-incoming"><span>{counts['incoming']}</span></div><h3>الوارد</h3><small>هذا الشهر: {month_in}</small></div>
<div class="stat-circle-card"><div class="stat-circle circle-outgoing"><span>{counts['outgoing']}</span></div><h3>الصادر</h3><small>هذا الشهر: {month_out}</small></div>
<div class="stat-circle-card"><div class="stat-circle circle-units"><span>{nu}</span></div><h3>الوحدات</h3></div>
<div class="stat-circle-card"><div class="stat-circle circle-storage"><span>{ns}</span></div><h3>أماكن التخزين</h3></div>
<div class="stat-circle-card"><div class="stat-circle circle-years"><span>{year_count}</span></div><h3>سنوات مؤرشفة</h3></div>
<div class="stat-circle-card"><div class="stat-circle circle-files"><span>{attachment_count}</span></div><h3>نسخ رقمية مرفقة</h3></div>
</div>{quick}{charts_html}<div class="card"><div class="flex-space mb-3"><h2>آخر السجلات المضافة</h2><div><a class="btn btn-success" href="/incoming/new">＋ وارد</a> <a class="btn btn-warning" href="/outgoing/new">＋ صادر</a></div></div><div class="table-responsive"><table><tr><th>النوع</th><th>الرقم</th><th>الموضوع</th><th>الوحدة</th><th>مكان الحفظ</th><th>إجراء</th></tr>{rh}</table></div></div>'''
        self.sendhtml(base('لوحة التحكم',c,'dashboard',err,ok,uid))

    def records_page(self,path,q,uid,f,s,err,ok):
        direction='incoming' if path=='/incoming' else 'outgoing'; title=s['incoming_title'] if direction=='incoming' else s['outgoing_title']; number=f['incoming_number'] if direction=='incoming' else f['outgoing_number']; subject=f['incoming_subject'] if direction=='incoming' else f['outgoing_subject']; date_label=f['incoming_date'] if direction=='incoming' else f['outgoing_date']; loc=f['physical_location'] if direction=='incoming' else f['outgoing_location']
        normal=q.get('q',[''])[0].strip(); rd=q.get('record_date',[''])[0].strip(); ad=q.get('added_date',[''])[0].strip(); unit=q.get('unit_id',[''])[0].strip(); relation=q.get('relation',['all'])[0].strip().lower()
        if relation not in ('all','linked','unlinked'): relation='all'
        x=db(); units=x.execute('SELECT u.* FROM units u ORDER BY u.name').fetchall(); cond=['r.direction=?']; par=[direction]
        if normal: cond.append('(r.record_number LIKE ? OR r.reference LIKE ?)'); par += [f'%{normal}%']*2
        if rd: cond.append('r.record_date=?'); par.append(rd)
        if ad: cond.append('date(r.created_at)=?'); par.append(ad)
        if unit: cond.append('r.unit_id=?'); par.append(unit)
        if relation=='linked': cond.append('EXISTS (SELECT 1 FROM record_links rl WHERE rl.incoming_id=r.id OR rl.outgoing_id=r.id)')
        elif relation=='unlinked': cond.append('NOT EXISTS (SELECT 1 FROM record_links rl WHERE rl.incoming_id=r.id OR rl.outgoing_id=r.id)')
        where=' AND '.join(cond)
        total=x.execute('SELECT COUNT(*) FROM records r WHERE '+where,par).fetchone()[0]
        raw_page=q.get('page',['1'])[0]; page=int(raw_page) if str(raw_page).isdigit() and int(raw_page)>0 else 1
        per_page=50
        allrows=x.execute('SELECT r.*,u.name unit_name FROM records r LEFT JOIN units u ON u.id=r.unit_id WHERE '+where,par).fetchall()
        allrows=sorted(allrows,key=lambda r:(natural_key(r['record_number']), r['id']))
        pages=max(1,(total+per_page-1)//per_page); page=min(page,pages); offset=(page-1)*per_page; rows=allrows[offset:offset+per_page]; x.close()
        uo='<option value="">كل الوحدات</option>'+''.join(f'<option value="{u["id"]}" {"selected" if str(u["id"])==unit else ""}>{esc(u["name"])}</option>' for u in units)
        rh=''.join(f'<tr><td><input type="checkbox" name="record_ids" value="{r["id"]}" aria-label="تحديد المراسلة {esc(record_number_display(r))}"></td><td>{esc(record_number_display(r))}</td><td>{esc(r["subject"])}</td><td>{esc(r["record_date"])}</td><td>{esc(r["unit_name"] or "—")}</td><td><strong>📍 {esc(r["physical_location"])}</strong></td><td><a class="btn btn-sm btn-primary" href="/records/view?id={r["id"]}">تفاصيل</a></td></tr>' for r in rows) or '<tr><td colspan="7" class="text-center">لا توجد نتائج.</td></tr>'
        addurl='/incoming/new' if direction=='incoming' else '/outgoing/new'; printurl=f'/print/records?direction={direction}'; pager=pagination_html(path,q,page,total,per_page)
        x_rec_tpl=db()
        cond_t=[]; val_t=[]
        if unit:
            cond_t.append('(t.unit_id = ? OR t.unit_id IS NULL OR t.unit_id = 0)')
            val_t.append(int(unit))
        if rd and re.match(r'^\d{4}',rd):
            cond_t.append('(t.year = ? OR t.year IS NULL OR t.year = 0)')
            val_t.append(int(rd[:4]))
        where_t=' AND '.join(cond_t) if cond_t else '1=1'
        tpl_rows=x_rec_tpl.execute('SELECT t.*, u.name as unit_name, s.name as storage_name FROM templates t LEFT JOIN units u ON u.id=t.unit_id LEFT JOIN storage_places s ON s.id=t.storage_place_id WHERE '+where_t+' ORDER BY t.name',val_t).fetchall()
        x_rec_tpl.close()
        if tpl_rows:
            tpl_btns=''.join(f'<a class="btn btn-sm" href="/templates/download?id={t["id"]}" style="background:#eef6fc;color:#1b6ca8;border:1px solid #c8e1f5;display:inline-flex;align-items:center;gap:4px;margin:2px;" title="تحميل أو فتح في Word">📝 {esc(t["name"])}{(" ("+esc(t["unit_name"])+")") if t["unit_name"] else ""}{(" — "+str(t["year"])) if t["year"] else ""}</a>' for t in tpl_rows[:25])
            tpl_panel=f'<div class="card" style="margin-bottom:15px;background:#fbfdfe;border-right:4px solid #16a085;"><div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;"><div><strong>📝 نماذج المراسلات (Word) الخاصة بالوحدة:</strong> <span class="muted">انقر لتحميل أو فتح النموذج:</span></div><div style="display:flex;flex-wrap:wrap;gap:4px;">{tpl_btns}</div></div></div>'
        else:
            tpl_panel=f'<div class="card" style="margin-bottom:15px;background:#fbfdfe;border-right:4px solid #16a085;"><div style="display:flex;justify-content:space-between;align-items:center;"><div><strong>📝 نماذج المراسلات (Word):</strong> <span class="muted">لا توجد نماذج مسجلة لهذه الوحدة حالياً. يمكنك إدارتها ورفعها من <a href="/settings#templates">الإعدادات</a>.</span></div></div></div>'

        c=f'''{tpl_panel}<div class="card"><div class="flex-space"><h2>{"📥" if direction=="incoming" else "📤"} {esc(title)}</h2><a class="btn btn-success" href="{addurl}">＋ إضافة {"وارد" if direction=="incoming" else "صادر"}</a></div><form method="GET" class="search-box" style="margin-top:18px"><input type="text" name="q" value="{esc(normal)}" class="form-control search-input-main" placeholder="ابحث بالرقم أو المرجع"><select name="unit_id" class="form-control" style="width:auto">{uo}</select><button class="btn btn-primary">بحث</button><a class="btn" href="{path}">مسح</a></form></div><div class="card"><h3>📅 تصفية بتاريخ الوثيقة الأصلي</h3><form method="GET" class="search-box"><input type="hidden" name="q" value="{esc(normal)}"><input type="hidden" name="unit_id" value="{esc(unit)}"><input type="date" name="record_date" value="{esc(rd)}" title="{esc(date_label)}"><button class="btn btn-primary">تصفية بتاريخ الوثيقة</button><a class="btn" href="{path}?q={urllib.parse.quote(normal)}&unit_id={urllib.parse.quote(unit)}">مسح هذا التاريخ</a></form><p class="muted">هذا هو التاريخ المكتوب على المراسلة الأصلية.</p></div><div class="card"><h3>🕒 تصفية بتاريخ الإضافة</h3><form method="GET" class="search-box"><input type="hidden" name="q" value="{esc(normal)}"><input type="hidden" name="unit_id" value="{esc(unit)}"><input type="date" name="added_date" value="{esc(ad)}" title="تاريخ الإضافة إلى النظام"><button class="btn btn-primary">تصفية بتاريخ الإضافة</button><a class="btn" href="{path}?q={urllib.parse.quote(normal)}&unit_id={urllib.parse.quote(unit)}">مسح هذا التاريخ</a></form><p class="muted">هذا هو اليوم الذي أُدخل فيه السجل إلى البرنامج.</p></div><div class="card"><div class="flex-space"><h3>النتائج ({len(rows)})</h3><a class="btn" href="{printurl}&header={urllib.parse.quote(s['print_header'] or title)}">🖨️ طباعة النتائج</a></div><form method="POST" action="/print/selected-pdfs" id="bulk_pdf_form" onsubmit="return validatePdfSelection()"><input type="hidden" name="direction" value="{direction}"><div class="flex-space mb-3"><span class="muted">حدد المراسلات التي تريد ملفات PDF الخاصة بها.</span><button class="btn btn-success" type="submit">🖨️ تجهيز طباعة PDF المحدد</button></div><div class="table-responsive"><table><tr><th><input type="checkbox" id="select_all_records" aria-label="تحديد الكل" onchange="document.querySelectorAll('#bulk_pdf_form input[name=record_ids]').forEach(c=>c.checked=this.checked)"></th><th>{esc(number)}</th><th>{esc(subject)}</th><th>{esc(date_label)}</th><th>{esc(f['unit'])}</th><th>{esc(loc)}</th><th>إجراء</th></tr>{rh}</table></div>{pager}</form></div><script>function validatePdfSelection(){{const n=document.querySelectorAll('#bulk_pdf_form input[name=record_ids]:checked').length;if(!n){{alert('حدد مراسلة واحدة على الأقل.');return false;}}return true;}}</script>'''
        self.sendhtml(base(title,c,'incoming' if direction=='incoming' else 'outgoing',err,ok,uid))

    def record_form(self,path,q,uid,f,s,err,ok):
        editing=path=='/records/edit'; rid=q.get('id',[''])[0] if editing else ''
        direction='incoming' if path.startswith('/incoming') else 'outgoing'
        x=db(); units=x.execute('SELECT u.id, u.name, s.name as location FROM units u LEFT JOIN storage_places s ON s.id=u.storage_place_id ORDER BY u.name').fetchall();
        refs=[r[0] for r in x.execute("SELECT DISTINCT reference FROM records WHERE TRIM(COALESCE(reference,''))<>'' ORDER BY reference LIMIT 300").fetchall()]
        subs=[r[0] for r in x.execute("SELECT DISTINCT subject FROM records WHERE TRIM(COALESCE(subject,''))<>'' ORDER BY subject LIMIT 300").fetchall()]
        data={}
        if rid:
            r=x.execute('SELECT * FROM records WHERE id=?',(rid,)).fetchone();
            if not r: x.close(); self.redir('/search?error='+urllib.parse.quote('السجل غير موجود.')); return
            data=dict(r)
            direction=r['direction']
        x.close()
        if direction=='incoming': num,sub,date,loc,notes=f['incoming_number'],f['incoming_subject'],f['incoming_date'],f['physical_location'],f['notes']; title='تعديل الوارد' if editing else 'إضافة وارد'
        else: num,sub,date,loc,notes=f['outgoing_number'],f['outgoing_subject'],f['outgoing_date'],f['outgoing_location'],f['outgoing_notes']; title='تعديل الصادر' if editing else 'إضافة صادر'
        unit_sel=str(data.get('unit_id') or '')
        selected_unit=next((u for u in units if str(u['id'])==unit_sel),None)
        selected_unit_name=(selected_unit['name'] if selected_unit else '')
        ref_values=''.join(f'<option value="{esc(v)}"></option>' for v in refs)
        sub_values=''.join(f'<option value="{esc(v)}"></option>' for v in subs)
        def helptext(k): return f'<small class="muted">{esc(s[k])}</small>' if s.get('show_'+k)=='1' else ''
        action='/records/edit?id='+urllib.parse.quote(rid) if editing else ('/incoming/new' if direction=='incoming' else '/outgoing/new')
        back='/incoming' if direction=='incoming' else '/outgoing'
        unit_json=json.dumps([{'name':u['name'],'id':u['id'],'location':u['location'] or ''} for u in units],ensure_ascii=False)
        c=f'''<div class="card"><h2>{esc(title)}</h2><form method="POST" action="{action}" id="record_form">
        <div class="stats-grid"><div class="form-group"><label>{esc(num)} *</label><input name="record_number" class="form-control" value="{esc(data.get('record_number',''))}" required>{helptext('number_help')}</div><div class="form-group"><label>{esc(f['reference'])}</label><input name="reference" list="reference_suggestions" class="form-control" value="{esc(data.get('reference',''))}"></div></div><div class="stats-grid"><div class="form-group"><label>{esc(sub)} (اختياري)</label><input name="subject" list="subject_suggestions" class="form-control" value="{esc(data.get('subject',''))}">{helptext('subject_help')}</div></div><div class="stats-grid"><div class="form-group"><label>{esc(date)} *</label><input type="date" name="record_date" class="form-control" value="{esc(data.get('record_date',''))}" required>{helptext('date_help')}</div><div class="form-group"><label>{esc(f['unit'])} *</label><div style="position:relative"><input name="unit_name" id="unit_name_top" class="form-control" value="{esc(selected_unit_name)}" placeholder="اكتب جزءاً من اسم الوحدة" autocomplete="off" required><input type="hidden" name="unit_id" id="unit_id_top" value="{esc(unit_sel)}"><div id="unit_choices" style="display:none;position:absolute;z-index:1000;top:100%;right:0;left:0;background:#fff;border:1px solid #bbb;border-radius:8px;max-height:240px;overflow-y:auto;box-shadow:0 6px 18px rgba(0,0,0,.15)"></div></div></div></div>
        <small class="muted">مكان الحفظ يُحدد تلقائياً من إعدادات الوحدة ولا يُدخل هنا.</small><div class="form-group"><label>{esc(loc)}</label><input id="physical_location_preview" class="form-control" value="{esc(data.get('physical_location',''))}" readonly><small class="muted">سيتم تعبئته تلقائياً عند اختيار الوحدة.</small></div><datalist id="reference_suggestions">{ref_values}</datalist><datalist id="subject_suggestions">{sub_values}</datalist><div class="form-group"><label>{esc(notes)}</label><textarea name="notes" class="form-control" rows="4">{esc(data.get('notes',''))}</textarea>{helptext('notes_help')}</div><button class="btn btn-success">💾 حفظ</button> <a class="btn" href="{back}">إلغاء</a></form></div><script>const unitName=document.getElementById('unit_name_top'),unitId=document.getElementById('unit_id_top'),locationInput=document.getElementById('physical_location_preview'),unitChoices=document.getElementById('unit_choices'),recordForm=document.getElementById('record_form');const unitMap={unit_json};function applyUnit(z){{if(!z)return;unitId.value=String(z.id);locationInput.value=z.location||'';unitName.value=z.name||'';unitChoices.style.display='none';}}function matches(z,v){{const q=(v||'').trim().toLocaleLowerCase();return !q || z.name.toLocaleLowerCase().includes(q);}}function renderUnitChoices(){{const v=unitName.value;const rows=unitMap.filter(z=>matches(z,v)).slice(0,50);unitChoices.innerHTML=rows.map(z=>'<button type="button" data-unit-id="'+z.id+'" style="display:block;width:100%;text-align:right;border:0;background:#fff;padding:10px 12px;cursor:pointer;border-bottom:1px solid #eee"><b>'+escapeUnit(z.name)+'</b></button>').join('');unitChoices.querySelectorAll('button').forEach(b=>b.addEventListener('mousedown',function(e){{e.preventDefault();const z=unitMap.find(a=>String(a.id)===String(this.dataset.unitId));applyUnit(z);}}));unitChoices.style.display=rows.length?'block':'none';}}function escapeUnit(v){{return String(v||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}}function resolveExactUnit(){{const v=unitName.value.trim();const z=unitMap.find(a=>a.name===v);if(z){{applyUnit(z);return true;}}return false;}}unitName.addEventListener('focus',renderUnitChoices);unitName.addEventListener('input',function(){{unitId.value='';locationInput.value='';renderUnitChoices();const exact=unitMap.find(a=>a.name===this.value.trim());if(exact)applyUnit(exact);}});unitName.addEventListener('change',resolveExactUnit);unitName.addEventListener('blur',function(){{setTimeout(function(){{resolveExactUnit();unitChoices.style.display='none';}},150);}});recordForm.addEventListener('submit',function(e){{if(!unitId.value)resolveExactUnit();if(!unitId.value){{e.preventDefault();unitName.focus();unitChoices.style.display='block';alert('يرجى اختيار الوحدة من قائمة الاقتراحات.');}}}});if(unitId.value){{const z=unitMap.find(a=>String(a.id)===String(unitId.value));if(z){{unitName.value=z.name;locationInput.value=z.location||'';}}}}</script>'''
        self.sendhtml(base(title,c,'incoming' if direction=='incoming' else 'outgoing',err,ok,uid))

    def view_record(self,q,uid,f,s):
        rid=q.get('id',[''])[0]; x=db(); r=x.execute('SELECT r.*,u.name unit_name FROM records r LEFT JOIN units u ON u.id=r.unit_id WHERE r.id=?',(rid,)).fetchone()
        if not r: x.close(); self.send_error(404); return
        incoming=r['direction']=='incoming'; title=s['incoming_title'] if incoming else s['outgoing_title']; number=f['incoming_number'] if incoming else f['outgoing_number']; subject=f['incoming_subject'] if incoming else f['outgoing_subject']; date=f['incoming_date'] if incoming else f['outgoing_date']; loc=f['physical_location'] if incoming else f['outgoing_location']; notes=f['notes'] if incoming else f['outgoing_notes']
        linked=[]
        try:
            ensure_record_links_schema(x)
            x.commit()
            if incoming:
                linked=x.execute('SELECT l.id link_id,r.*,u.name unit_name FROM record_links l JOIN records r ON r.id=l.outgoing_id LEFT JOIN units u ON u.id=r.unit_id WHERE l.incoming_id=? ORDER BY r.id',(rid,)).fetchall()
            else:
                linked=x.execute('SELECT l.id link_id,r.*,u.name unit_name FROM record_links l JOIN records r ON r.id=l.incoming_id LEFT JOIN units u ON u.id=r.unit_id WHERE l.outgoing_id=? ORDER BY r.id',(rid,)).fetchall()
        except Exception:
            linked=[]
        try:
            candidates=find_link_candidates(x,r,current_session_id(self))
        except Exception:
            candidates=[]
        linked_ids={z['id'] for z in linked}
        if incoming:
            current_linked={z['outgoing_id'] for z in x.execute('SELECT outgoing_id FROM record_links WHERE incoming_id=?',(rid,)).fetchall()}
        else:
            current_linked={z['incoming_id'] for z in x.execute('SELECT incoming_id FROM record_links WHERE outgoing_id=?',(rid,)).fetchall()}
        candidates=[z for z in candidates if z['row']['id'] not in linked_ids and z['row']['id'] not in current_linked]
        attachments=x.execute('SELECT * FROM attachments WHERE record_id=? ORDER BY id',(rid,)).fetchall()
        unit_templates=[]
        if r['unit_id']:
            unit_templates=x.execute('SELECT * FROM templates WHERE unit_id=? OR unit_id IS NULL OR unit_id=0 ORDER BY name',(r['unit_id'],)).fetchall()
        x.close()
        unit_tpl_section=''
        if unit_templates:
            unit_tpl_links=''.join(f'<a class="btn btn-sm" href="/templates/download?id={t["id"]}" style="background:#eef6fc;color:#1b6ca8;border:1px solid #c8e1f5;margin:3px;" title="تحميل أو فتح في Word">📝 {esc(t["name"])}{(" — "+str(t["year"])) if t["year"] else ""}</a>' for t in unit_templates)
            unit_tpl_section=f'<div class="card"><h3>📝 نماذج المراسلات (Word) الخاصة بـ {esc(r["unit_name"] or "الوحدة")}</h3><div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;">{unit_tpl_links}</div></div>'

        edit=f'<a class="btn btn-primary" href="/records/edit?id={r["id"]}">تعديل</a>'
        delete=f'<form method="POST" action="/records/delete" style="display:inline" onsubmit="return confirm({json.dumps(s["confirm_delete"],ensure_ascii=False)})"><input type="hidden" name="id" value="{r["id"]}"><button class="btn btn-danger">حذف</button></form>'
        printlink=f'/print/record?id={r["id"]}'
        if incoming:
            linked_html=''.join(f'<li><a href="/records/view?id={z["id"]}">📤 الصادر رقم {esc(z["record_number"])}/{esc(z["record_year"])}</a> — {esc(z["subject"])} <form method="POST" action="/records/unlink" style="display:inline"><input type="hidden" name="link_id" value="{z["link_id"]}"><input type="hidden" name="incoming_id" value="{r["id"]}"><input type="hidden" name="return_id" value="{r["id"]}"><button class="btn btn-sm">🔓 فك الربط</button></form></li>' for z in linked)
            cand_html=''.join(f'<li><span class="badge">درجة {z["score"]}</span> 📤 <a href="/records/view?id={z["row"]["id"]}"><b>الصادر رقم {esc(z["row"]["record_number"])}/{esc(z["row"]["record_year"])}</b></a> — {esc(z["row"]["subject"])} <small class="muted">({esc(z["reason"])})</small> <form method="POST" action="/records/link" style="display:inline"><input type="hidden" name="incoming_id" value="{r["id"]}"><input type="hidden" name="outgoing_id" value="{z["row"]["id"]}"><button class="btn btn-sm btn-success">ربط بهذا الوارد</button></form></li>' for z in candidates)
        else:
            linked_html=''.join(f'<li><a href="/records/view?id={z["id"]}">📥 الوارد رقم {esc(z["record_number"])}/{esc(z["record_year"])}</a> — {esc(z["subject"])} <form method="POST" action="/records/unlink" style="display:inline"><input type="hidden" name="link_id" value="{z["link_id"]}"><input type="hidden" name="outgoing_id" value="{r["id"]}"><input type="hidden" name="return_id" value="{r["id"]}"><button class="btn btn-sm">🔓 فك الربط</button></form></li>' for z in linked)
            cand_html=''.join(f'<li><span class="badge">درجة {z["score"]}</span> 📥 <a href="/records/view?id={z["row"]["id"]}"><b>الوارد رقم {esc(z["row"]["record_number"])}/{esc(z["row"]["record_year"])}</b></a> — {esc(z["row"]["subject"])} <small class="muted">({esc(z["reason"])})</small> <form method="POST" action="/records/link" style="display:inline"><input type="hidden" name="incoming_id" value="{z["row"]["id"]}"><input type="hidden" name="outgoing_id" value="{r["id"]}"><button class="btn btn-sm btn-success">ربط بهذا الصادر</button></form></li>' for z in candidates)
        relation='<div class="card"><h3>🔗 السجلات المرتبطة</h3>'
        relation += '<ul>'+linked_html+'</ul>' if linked_html else '<p class="muted">لا توجد مراسلات مرتبطة بهذا الملف حاليًا.</p>'
        relation += '<hr><h3>🔎 احتمالات الارتباط</h3>'
        if candidates:
            relation += '<p class="muted">أعلى احتمالين موثوقين فقط.</p><ul>'+cand_html+'</ul>'
        else:
            relation += '<p class="muted">لا توجد احتمالات ارتباط قوية حاليًا.</p>'
        relation += '</div>'
        attach_items=[]
        for a in attachments:
            aid=str(a['id']); name=esc(a['original_name']); size='%.1f' % (int(a['size_bytes'])/1024.0)
            attach_items.append('<li><a href="/attachments/open?id='+aid+'" target="_blank">📎 '+name+'</a> <span class="muted">('+size+' KB)</span> <a class="btn btn-sm" href="/attachments/open?id='+aid+'&download=1">فتح/حفظ</a> <a class="btn btn-sm" href="/attachments/open?id='+aid+'&print=1" target="_blank">🖨️ طباعة</a> <form method="POST" action="/attachments/delete" style="display:inline" onsubmit="return confirm(\'حذف النسخة الرقمية فقط؟\')"><input type="hidden" name="id" value="'+aid+'"><button class="btn btn-sm btn-danger">حذف الملف</button></form></li>')
        attachment_section='<div class="card"><h3>?? النسخ الرقمية / الملفات الممسوحة ضوئيًا</h3>'
        attachment_section += ('<ul>'+''.join(attach_items)+'</ul>') if attach_items else '<p class="muted">لا توجد نسخ رقمية مرفقة. الإضافة اختيارية ويمكن القيام بها لاحقًا.</p>'
        attachment_section += '<form method="POST" action="/attachments/add" enctype="multipart/form-data" class="search-box"><input type="hidden" name="record_id" value="'+str(r['id'])+'"><input type="file" name="file" accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf" required><button class="btn btn-success">➕ إضافة نسخة رقمية</button></form><p class="muted">النسخ الرقمية اختيارية ولا تمنع إضافة الوارد أو الصادر.</p></div>'

        c=f'''<div class="card"><div class="flex-space"><h2>{"📥" if incoming else "📤"} {esc(title)}</h2><div>{edit} {delete} <a class="btn" href="{printlink}">🖨️ طباعة</a></div></div><table class="detail-table"><tr><th>{esc(number)}</th><td>{esc(r["record_number"])}/{esc(r["record_year"])}</td></tr><tr><th>{esc(f['reference'])}</th><td>{esc(r['reference'] or '—')}</td></tr><tr><th>{esc(subject)}</th><td>{esc(r['subject'])}</td></tr><tr><th>{esc(date)}</th><td>{esc(r['record_date'])}</td></tr><tr><th>{esc(f['unit'])}</th><td>{esc(r['unit_name'] or '—')}</td></tr><tr><th>{esc(loc)}</th><td><strong>📍 {esc(r['physical_location'])}</strong></td></tr><tr><th>{esc(notes)}</th><td>{esc(r['notes'] or '—')}</td></tr><tr><th>{esc(f['created_at'])}</th><td>{esc(r['created_at'])}</td></tr></table></div>{relation}{attachment_section}{unit_tpl_section}'''
        self.sendhtml(base(title,c,'incoming' if incoming else 'outgoing',uid=uid))

    def search_page(self,q,uid,f,s,err,ok):
        direction=q.get('direction',['all'])[0]; normal=q.get('q',[''])[0].strip(); year=q.get('year',[''])[0].strip(); rdf=q.get('record_date_from',[''])[0].strip(); rdt=q.get('record_date_to',[''])[0].strip(); adf=q.get('added_date_from',[''])[0].strip(); adt=q.get('added_date_to',[''])[0].strip(); unit=q.get('unit_id',[''])[0].strip()
        x=db(); units=x.execute('SELECT u.id, u.name FROM units u ORDER BY u.name').fetchall(); cond=[];par=[]
        if direction in ('incoming','outgoing'): cond.append('r.direction=?');par.append(direction)
        if normal:
            if '/' in normal:
                parts = normal.split('/')
                if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                    cond.append('((r.record_number=? AND r.record_year=?) OR r.reference LIKE ?)')
                    par += [parts[0].strip(), int(parts[1].strip()), f'%{normal}%']
                else:
                    cond.append('(r.record_number LIKE ? OR r.reference LIKE ? OR r.subject LIKE ? OR r.physical_location LIKE ? OR r.notes LIKE ? OR COALESCE(u.name,\'\') LIKE ?)')
                    par += [f'%{normal}%']*6
            else:
                cond.append('(r.record_number LIKE ? OR r.reference LIKE ? OR r.subject LIKE ? OR r.physical_location LIKE ? OR r.notes LIKE ? OR COALESCE(u.name,\'\') LIKE ?)')
                par += [f'%{normal}%']*6
        if year: cond.append('r.record_year=?');par.append(year)
        if rdf: cond.append('r.record_date>=?');par.append(rdf)
        if rdt: cond.append('r.record_date<=?');par.append(rdt)
        if adf: cond.append('date(r.created_at)>=?');par.append(adf)
        if adt: cond.append('date(r.created_at)<=?');par.append(adt)
        if unit: cond.append('r.unit_id=?');par.append(unit)
        where=' WHERE '+' AND '.join(cond) if cond else ''
        total=x.execute('SELECT COUNT(*) FROM records r LEFT JOIN units u ON u.id=r.unit_id'+where,par).fetchone()[0]
        raw_page=q.get('page',['1'])[0]; page=int(raw_page) if str(raw_page).isdigit() and int(raw_page)>0 else 1
        per_page=50; pages=max(1,(total+per_page-1)//per_page); page=min(page,pages); offset=(page-1)*per_page
        rows=x.execute('SELECT r.*,u.name unit_name FROM records r LEFT JOIN units u ON u.id=r.unit_id'+where+' ORDER BY CAST(r.record_number AS INTEGER) ASC, r.id ASC LIMIT ? OFFSET ?',par+[per_page,offset]).fetchall()
        years=[r[0] for r in x.execute("SELECT DISTINCT record_year FROM records ORDER BY record_year DESC").fetchall()]
        x.close()
        uo='<option value="">كل الوحدات</option>'+''.join(f'<option value="{u["id"]}" {"selected" if unit==str(u["id"]) else ""}>{esc(u["name"])}</option>' for u in units)
        rh=''.join(f'<tr><td>{"وارد" if r["direction"]=="incoming" else "صادر"}</td><td>{esc(r["record_number"])}/{esc(r["record_year"])}</td><td>{esc(r["reference"] or "—")}</td><td>{esc(r["subject"])}</td><td>{esc(r["record_date"])}</td><td>{esc(r["unit_name"] or "—")}</td><td>📍 {esc(r["physical_location"])}</td><td><a class="btn btn-sm btn-primary" href="/records/view?id={r["id"]}">عرض</a></td></tr>' for r in rows) or '<tr><td colspan="8" class="text-center">لا توجد نتائج.</td></tr>'
        pager=pagination_html('/search',q,page,total,per_page)
        yo='<option value="">كل السنوات</option>'+''.join(f'<option value="{esc(y)}" {"selected" if str(y)==year else ""}>{esc(y)}</option>' for y in years)
        c=f'''<div class="card"><h2>🔎 البحث في الأرشيف</h2><form method="GET" class="search-box"><select name="direction" class="form-control" style="width:auto"><option value="all">الوارد والصادر</option><option value="incoming">الوارد</option><option value="outgoing">الصادر</option></select><select name="year" class="form-control" style="width:auto">{yo}</select><input name="q" value="{esc(normal)}" class="form-control search-input-main" placeholder="{esc(s['search_placeholder'])}"><select name="unit_id" class="form-control" style="width:auto">{uo}</select><button class="btn btn-primary">بحث</button><a class="btn" href="/search">مسح</a></form></div><div class="card"><h3>📅 تاريخ الوثيقة</h3><form method="GET" class="search-box"><input type="hidden" name="direction" value="{esc(direction)}"><input type="hidden" name="year" value="{esc(year)}"><input type="hidden" name="q" value="{esc(normal)}"><input type="hidden" name="unit_id" value="{esc(unit)}"><label>من <input type="date" name="record_date_from" value="{esc(rdf)}"></label><label>إلى <input type="date" name="record_date_to" value="{esc(rdt)}"></label><button class="btn btn-primary">تصفية بتاريخ الوثيقة</button></form><p class="muted">التاريخ المكتوب على المراسلة الأصلية.</p></div><div class="card"><h3>🕒 تاريخ الإضافة إلى النظام</h3><form method="GET" class="search-box"><input type="hidden" name="direction" value="{esc(direction)}"><input type="hidden" name="year" value="{esc(year)}"><input type="hidden" name="q" value="{esc(normal)}"><input type="hidden" name="unit_id" value="{esc(unit)}"><label>من <input type="date" name="added_date_from" value="{esc(adf)}"></label><label>إلى <input type="date" name="added_date_to" value="{esc(adt)}"></label><button class="btn btn-primary">تصفية بتاريخ الإضافة</button></form><p class="muted">اليوم الذي أُدخلت فيه الوثيقة إلى البرنامج.</p></div><div class="card"><h3>النتائج: {len(rows)}</h3><div class="table-responsive"><table><tr><th>النوع</th><th>الرقم</th><th>المرجع</th><th>الموضوع</th><th>التاريخ</th><th>الوحدة</th><th>مكان الحفظ</th><th>إجراء</th></tr>{rh}</table></div>{pager}</div>'''
        self.sendhtml(base('البحث',c,'search',err,ok,uid))

    def reports_page(self,q,uid,f,s,err,ok):
        x=db(); units=x.execute('SELECT u.* FROM units u ORDER BY u.name').fetchall(); x.close()
        uo='<option value="">اختر الوحدة</option>'+''.join(f'<option value="{u["id"]}">{esc(u["name"])}</option>' for u in units)
        c=f'''<div class="card"><h2>📈 التقارير والطباعة والتصدير</h2><p class="muted">يمكنك اختيار البريد أو البرقيات أو كليهما، والطباعة لوحدة منفردة أو لجميع السجلات. يمكنك أيضًا اختيار الأعمدة وترتيب النتائج، ثم التصدير إلى Excel.</p>
        <form method="GET" action="/print/records" id="reportForm">
        <div class="form-group"><label>العنوان المخصص أعلى الطباعة</label><input name="header" class="form-control" value="{esc(s["print_header"])}"></div>
        <div class="form-group"><label>الاتجاه</label><select name="direction" class="form-control"><option value="all">وارد وصادر</option><option value="incoming">الوارد فقط</option><option value="outgoing">الصادر فقط</option></select></div>
        <div class="form-group"><label>نطاق البيانات</label><select name="print_type" id="ptype" class="form-control" onchange="toggleTargets()"><option value="unit">وحدة منفردة</option><option value="all">كل السجلات</option></select></div>
        <div class="form-group" id="unit_box"><label>الوحدة</label><select name="unit_id" class="form-control">{uo}</select></div>
        <div class="stats-grid"><div class="form-group"><label>ترتيب النتائج</label><select name="sort" class="form-control"><option value="number">رقم المراسلة</option><option value="date">تاريخ الوثيقة</option><option value="added">تاريخ الإضافة</option><option value="unit">الوحدة</option><option value="subject">الموضوع</option><option value="reference">المرجع</option><option value="direction">الاتجاه</option></select></div><div class="form-group"><label>ترتيب</label><select name="order" class="form-control"><option value="asc">تصاعدي</option><option value="desc">تنازلي</option></select></div></div>
        <div class="card" style="margin-top:12px"><h3>الأعمدة الظاهرة في الطباعة وExcel</h3><div class="search-box" style="gap:16px;flex-wrap:wrap">
        <label><input type="checkbox" name="col" value="number" checked> الرقم</label><label><input type="checkbox" name="col" value="reference" checked> المرجع</label><label><input type="checkbox" name="col" value="subject" checked> الموضوع</label><label><input type="checkbox" name="col" value="date" checked> التاريخ</label><label><input type="checkbox" name="col" value="unit" checked> الوحدة</label><label><input type="checkbox" name="col" value="location" checked> مكان الحفظ</label><label><input type="checkbox" name="col" value="notes"> الملاحظات</label><label><input type="checkbox" name="col" value="created" checked> تاريخ الإضافة</label><label><input type="checkbox" name="col" value="direction"> الاتجاه/النوع</label></div></div>
        <div class="search-box"><button class="btn btn-primary" type="submit">🖨️ إنشاء صفحة الطباعة</button><button class="btn btn-success" type="button" onclick="exportExcel()">📊 تصدير إلى Excel</button></div></form></div>
        <script>function toggleTargets(){{let t=document.getElementById('ptype').value;document.getElementById('unit_box').style.display=t==='unit'?'block':'none';}}function exportExcel(){{const f=document.getElementById('reportForm');const p=new URLSearchParams(new FormData(f));p.set('header',f.querySelector('[name=header]').value);window.location.href='/export/excel?'+p.toString();}}toggleTargets();</script>'''
        self.sendhtml(base('التقارير والطباعة',c,'reports',err,ok,uid))

    def _report_query(self,q):
        direction=q.get('direction',['all'])[0]
        unit=q.get('unit_id',[''])[0]
        ptype=q.get('print_type',['all'])[0]
        sort=q.get('sort',['number'])[0]
        order=q.get('order',['asc'])[0]
        cond=[]; par=[]
        if direction in ('incoming','outgoing'): cond.append('r.direction=?'); par.append(direction)
        if ptype=='unit' and unit: cond.append('r.unit_id=?'); par.append(unit)
        sortmap={'number':'r.record_number','date':'r.record_date','added':'r.created_at','unit':'COALESCE(u.name,\'\')','subject':'r.subject','reference':'r.reference','direction':'r.direction'}
        sort_sql=sortmap.get(sort,'r.record_number')
        direction_sql='DESC' if order=='desc' else 'ASC'
        if sort=='number': order_sql=f"CAST(r.record_number AS INTEGER) {direction_sql}"
        else: order_sql=f'{sort_sql} {direction_sql}, r.id {direction_sql}'
        where=' WHERE '+' AND '.join(cond) if cond else ''
        return where,par,order_sql

    def _report_scope(self,q,x):
        ptype=q.get('print_type',['all'])[0]; unit=q.get('unit_id',[''])[0]
        if ptype=='unit' and unit:
            rr=x.execute('SELECT name FROM units WHERE id=?',(unit,)).fetchone(); return 'الوحدة: '+(rr['name'] if rr else '—')
        return 'كل السجلات'

    def export_excel(self,q):
        u=self.user()
        if not u: self.redir('/login'); return
        cols=q.get('col',[]) or ['number','reference','subject','date','unit','location','created']
        canonical=['number','reference','subject','date','unit','location','notes','created','direction']
        labels={'serial':'م','direction':'النوع','number':'الرقم','reference':'المرجع','subject':'الموضوع','date':'التاريخ','unit':'الوحدة','location':'مكان الحفظ','notes':'الملاحظات','created':'تاريخ الإضافة'}
        allowed=[c for c in canonical if c in cols]
        if not allowed: allowed=['number','subject','date']
        x=db(); where,par,order_sql=self._report_query(q); rows=x.execute('SELECT r.*,u.name unit_name FROM records r LEFT JOIN units u ON u.id=r.unit_id '+where+' ORDER BY '+order_sql,par).fetchall(); x.close()
        headers=[labels['serial']]+[labels[c] for c in allowed]
        data=[]
        for idx,r in enumerate(rows,1):
            dir_label = "وارد" if r['direction']=='incoming' else "صادر"
            full_type = dir_label
            vals={'direction':full_type,'number':f"{r['record_number']}/{r['record_year']}",'reference':r['reference'] or '—','subject':r['subject'],'date':r['record_date'],'unit':r['unit_name'] or '—','location':r['physical_location'],'notes':r['notes'] or '—','created':r['created_at']}
            data.append([idx]+[vals[c] for c in allowed])
        blob=build_xlsx(headers,data,'الأرشيف')
        stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filename=f'archive_export_{stamp}.xlsx'
        self.send_response(200); self.send_header('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'); self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+urllib.parse.quote(filename)); self.send_header('Content-Length',str(len(blob))); self.end_headers(); self.wfile.write(blob)

    def backup_download(self):
        u=self.user()
        if not u: self.redir('/login'); return
        if u['role']!='admin': self.redir('/?error='+urllib.parse.quote('المدير فقط يستطيع إنشاء النسخة الاحتياطية.')); return
        bio=io.BytesIO(); stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        archive_root=resolve_archive_root()
        with zipfile.ZipFile(bio,'w',zipfile.ZIP_DEFLATED) as z:
            if os.path.isfile(DB_FILE): z.write(DB_FILE,'archive.db')
            z.writestr('archive_files/','')
            if os.path.isdir(archive_root):
                for root,dirs,files in os.walk(archive_root):
                    rel_dir=os.path.relpath(root,archive_root)
                    if rel_dir!='.':
                        z.writestr('archive_files/'+rel_dir.replace(os.sep,'/')+'/', '')
                    for fn in files:
                        full=os.path.join(root,fn)
                        rel=os.path.relpath(full,archive_root).replace(os.sep,'/')
                        z.write(full,'archive_files/'+rel)
        blob=bio.getvalue(); name='archive_backup_'+stamp+'.zip'
        self.send_response(200); self.send_header('Content-Type','application/zip'); self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+urllib.parse.quote(name)); self.send_header('Content-Length',str(len(blob))); self.end_headers(); self.wfile.write(blob)

    def attachment_open(self,q):
        u=self.user()
        if not u: self.redir('/login'); return
        aid=q.get('id',[''])[0]
        x=db(); a=x.execute('SELECT * FROM attachments WHERE id=?',(aid,)).fetchone(); x.close()
        if not a: self.send_error(404); return
        path=resolve_attachment_path(a['relative_path'])
        if not os.path.isfile(path): self.send_error(404); return
        data=open(path,'rb').read()
        if q.get('print',[''])[0]=='1' and a['mime_type'].startswith('image/'):
            import base64
            b64=base64.b64encode(data).decode('ascii')
            out='<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>طباعة النسخة الرقمية</title><style>body{text-align:center;margin:0;padding:20px}img{max-width:100%;height:auto}@media print{button{display:none}body{padding:0}}</style></head><body><button onclick="window.print()">🖨️ طباعة</button><br><img src="data:'+a['mime_type']+';base64,'+b64+'" onload="window.print()"></body></html>'
            self.sendhtml(out); return
        self.send_response(200); self.send_header('Content-Type',a['mime_type'])
        disp='attachment' if q.get('download',[''])[0]=='1' else 'inline'
        self.send_header('Content-Disposition',f"{disp}; filename*=UTF-8''"+urllib.parse.quote(a['original_name']))
        self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)

    def quick_links_page(self,uid,f,s,err,ok):
        x=db(); rows=x.execute('SELECT * FROM quick_links ORDER BY sort_order,id').fetchall(); x.close(); admin=self.admin(self.user())
        def quick_row(r):
            if admin:
                actions = (f'<a class="btn btn-sm" href="/quick-links/edit?id={r["id"]}">تعديل</a> '
                           f'<form method="POST" action="/quick-links/delete" style="display:inline">'
                           f'<input type="hidden" name="id" value="{r["id"]}">'
                           f'<button class="btn btn-sm btn-danger">حذف</button></form>')
            else:
                actions = '<span class="muted">الفتح متاح للجميع</span>'
            return (f'<tr><td>{icon_html(r["icon"] or "file")} {esc(r["title"])}</td>'
                    f'<td style="direction:ltr;text-align:left">{esc(r["path"])}</td>'
                    f'<td><span class="color-dot" style="background:{esc(r["color"])}"></span>{esc(r["color"])}</td>'
                    f'<td>{actions}</td></tr>')
        rh=''.join(quick_row(r) for r in rows) or '<tr><td colspan="4">لا توجد أزرار.</td></tr>'
        form=''
        if admin:
            q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query); editid=q.get('id',[''])[0] if '/edit' in self.path else ''; picked=q.get('picked_path',[''])[0]; x=db(); old=x.execute('SELECT * FROM quick_links WHERE id=?',(editid,)).fetchone() if editid else None; x.close(); old=old or {'title':'','path':'','icon':'📄','color':'#16a085','sort_order':0};
            if picked: old=dict(old); old['path']=picked
            action='/quick-links/edit?id='+urllib.parse.quote(editid) if editid else '/quick-links/add'
            icon_palette = ''.join(f'<button type="button" class="icon-choice" title="{label}" data-icon="{esc(key)}" onclick="document.getElementById(\'quick_icon\').value=this.dataset.icon">{icon_html(key)}</button>' for key,label in QUICK_ICON_LABELS.items())
            form=f'''<div class="card"><h2>{"تعديل زر الفتح السريع" if editid else "إضافة زر فتح سريع"}</h2><form method="POST" action="{action}"><div class="form-group"><label>اسم الزر</label><input name="title" class="form-control" value="{esc(old['title'])}" required></div><div class="form-group"><label>مسار الملف</label><div class="search-box"><input id="quick_path" name="path" class="form-control" value="{esc(old['path'])}" required><a class="btn" href="/quick-links/pick?mode=file&return={urllib.parse.quote(action)}">📄 اختيار ملف</a><a class="btn" href="/quick-links/pick?mode=dir&return={urllib.parse.quote(action)}">📁 اختيار مجلد</a></div></div><div class="stats-grid"><div class="form-group"><label>الأيقونة</label><input id="quick_icon" name="icon" class="form-control" value="{esc(old['icon'])}"><div class="icon-palette">{icon_palette}</div><small class="muted">اختر أيقونة جاهزة أو اكتب رمزاً خاصاً.</small></div><div class="form-group"><label>لون الزر</label><input type="color" name="color" class="form-control" value="{esc(old['color'])}"></div></div><div class="form-group"><label>ترتيب الظهور</label><input type="number" name="sort_order" class="form-control" value="{esc(old['sort_order'])}"></div><button class="btn btn-success">حفظ الزر</button> <a class="btn" href="/quick-links">إلغاء</a></form></div>'''
        c=f'''<div class="card"><h2>⚡ {esc(s.get('quick_title','الفتح السريع'))}</h2><p class="muted">الأزرار تفتح Word وExcel وPDF وأي ملف آخر بواسطة البرنامج الافتراضي في Windows. جميع المستخدمين يستطيعون الفتح، والمدير وحده يدير الأزرار والمسارات.</p></div>{form}<div class="card"><div class="table-responsive"><table><tr><th>الزر</th><th>المسار</th><th>اللون</th><th>إجراء</th></tr>{rh}</table></div></div>'''
        self.sendhtml(base(s.get('quick_title','الفتح السريع'),c,'quick',err,ok,uid))

    def quick_links_pick(self,uid):
        if not self.admin(self.user()): self.redir('/?error='+urllib.parse.quote('هذه العملية متاحة للمدير فقط.')); return
        path=''
        try:
            import tkinter as tk
            from tkinter import filedialog
            root=tk.Tk(); root.withdraw(); root.attributes('-topmost',True)
            mode=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('mode',['file'])[0]
            if mode=='dir': path=filedialog.askdirectory(title='اختيار مجلد للفتح السريع')
            else: path=filedialog.askopenfilename(title='اختيار ملف للفتح السريع',filetypes=[('كل الملفات','*.*'),('Word','*.doc;*.docx;*.docm'),('Excel','*.xls;*.xlsx;*.xlsm;*.csv'),('PDF','*.pdf')])
            root.destroy()
        except Exception: pass
        target=urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('return',['/quick-links'])[0])
        if path:
            sep='&' if '?' in target else '?'; self.redir(target+sep+'picked_path='+urllib.parse.quote(path))
        else:self.redir(target)

    def quick_links_open(self,q,uid):
        lid=q.get('id',[''])[0]; x=db(); r=x.execute('SELECT * FROM quick_links WHERE id=?',(lid,)).fetchone(); x.close()
        if not r:self.send_error(404);return
        path=os.path.expandvars(os.path.expanduser(r['path']))
        if not os.path.exists(path): self.redir('/quick-links?error='+urllib.parse.quote('المسار غير موجود: '+path));return
        try:
            if os.name=='nt': os.startfile(path)
            else: webbrowser.open('file://'+urllib.parse.quote(os.path.abspath(path)))
            audit(uid,'فتح','فتح سريع',lid,r['title']); self.redir('/?success='+urllib.parse.quote('تم فتح: '+r['title']))
        except Exception as e:self.redir('/quick-links?error='+urllib.parse.quote('تعذر فتح الملف: '+str(e)))

    def archive_migration_preview_page(self,uid,new_root,err,ok):
        old_root=resolve_archive_root()
        new_root=resolve_archive_root(new_root)
        plan=attachment_migration_plan(new_root)
        blocked=[item for item in plan if item['status'] not in ('في مكانه الصحيح','سيُنقل بعد التأكيد')]
        move_count=sum(1 for item in plan if item['status']=='سيُنقل بعد التأكيد')
        same_root=os.path.normcase(os.path.realpath(old_root))==os.path.normcase(os.path.realpath(new_root))
        rows_html=''.join(f'<tr><td>{esc(str(item.get("record_number") or ""))}/{esc(str(item.get("record_year") or ""))}</td><td>{esc(item.get("original_name") or "")}</td><td><code dir="ltr">{esc(item.get("source") or "")}</code></td><td><code dir="ltr">{esc(item.get("destination") or "")}</code></td><td>{esc(item["status"])}</td></tr>' for item in plan) or '<tr><td colspan="5">لا توجد مرفقات مسجلة تحتاج إلى النقل.</td></tr>'
        if blocked:
            action_html='<div class="alert alert-warning">توجد ملفات مفقودة أو تعارضات. لم يُنقل أي ملف. عالج الحالات الموضحة ثم أعد المعاينة.</div>'
        elif not plan:
            action_html='<p>لا توجد مرفقات مسجلة؛ يمكنك تغيير المسار من إعدادات التخزين مباشرة.</p>'
        elif same_root and not move_count:
            action_html='<p>المرفقات الموجودة في أماكنها الصحيحة بالفعل؛ لا توجد تغييرات مطلوبة.</p>'
        else:
            action_html=f'''<form method="POST" action="/storage/migrate-attachments" onsubmit="return confirm('سيتم نسخ المرفقات والتحقق منها وتحديث قاعدة البيانات ثم حذف النسخ المصدرية غير اللازمة. هل راجعت المعاينة؟');">
<input type="hidden" name="new_root" value="{esc(new_root)}">
<label class="check-row"><input type="checkbox" name="confirm_migrate" value="1" required> أوافق على تنظيم المرفقات المسجلة ونقلها بعد التحقق من سلامة النسخ.</label>
<button class="btn btn-success">تنفيذ النقل الآمن وتحديث المسار</button></form>'''
        content=f'''<div class="card"><h2>معاينة تنظيم المرفقات الموجودة</h2>
<p><strong>المسار الحالي:</strong> <code dir="ltr">{esc(old_root)}</code></p>
<p><strong>المسار المقترح:</strong> <code dir="ltr">{esc(new_root)}</code></p>
<p>لن تُنقل الملفات في هذه الصفحة. تُحسب الوجهات من بيانات السجلات والوحدات ومكان التخزين في قاعدة البيانات، ولا تُعامل المجلدات غير المعروفة على أنها وحدات.</p>
<div class="stats-grid"><div class="stat-card"><h3>مرفقات مسجلة</h3><div class="num">{len(plan)}</div></div><div class="stat-card"><h3>تحتاج إلى نقل</h3><div class="num">{move_count}</div></div><div class="stat-card"><h3>حالات مانعة</h3><div class="num">{len(blocked)}</div></div></div>
</div><div class="card"><div class="table-responsive"><table><tr><th>رقم السجل</th><th>اسم الملف الأصلي</th><th>المصدر</th><th>الوجهة المقترحة</th><th>الحالة</th></tr>{rows_html}</table></div></div>
<div class="card"><h3>تأكيد التنفيذ</h3>{action_html}<a class="btn" href="/settings">إلغاء والعودة إلى الإعدادات</a></div>'''
        self.sendhtml(base('معاينة تنظيم المرفقات',content,'settings',err,ok,uid))

    def archive_structure_page(self,uid,f,s,err,ok):
        root=resolve_archive_root()
        c=db()
        rows=c.execute('''SELECT DISTINCT r.record_year,u.name AS unit_name,s.name AS storage_name
                          FROM records r LEFT JOIN units u ON u.id=r.unit_id
                          LEFT JOIN storage_places s ON s.id=u.storage_place_id
                          ORDER BY r.record_year DESC,s.name,u.name''').fetchall()
        units_without_records=c.execute('''SELECT u.name AS unit_name,s.name AS storage_name FROM units u
                                           LEFT JOIN storage_places s ON s.id=u.storage_place_id
                                           WHERE NOT EXISTS(SELECT 1 FROM records r WHERE r.unit_id=u.id)
                                           ORDER BY s.name,u.name''').fetchall()
        c.close()
        known_years={safe_folder_component(r['record_year']) for r in rows}
        row_html=[]
        expected=[]
        for row in rows:
            year=safe_folder_component(row['record_year'])
            place=safe_folder_component(row['storage_name'])
            unit=safe_folder_component(row['unit_name'])
            base_path=os.path.join(root,year,place,unit)
            incoming=os.path.join(base_path,'وارد')
            outgoing=os.path.join(base_path,'صادر')
            in_state,in_conflict,in_missing=archive_directory_status(root,incoming)
            out_state,out_conflict,out_missing=archive_directory_status(root,outgoing)
            expected.extend([(incoming,in_state,in_conflict,in_missing),(outgoing,out_state,out_conflict,out_missing)])
            row_html.append(f'<tr><td>{esc(year)}</td><td>{esc(row["storage_name"] or "غير محدد")}</td><td>{esc(row["unit_name"] or "غير محدد")}</td><td>{esc(in_state)}{("<br><small>"+esc(in_conflict)+"</small>") if in_conflict else ""}</td><td>{esc(out_state)}{("<br><small>"+esc(out_conflict)+"</small>") if out_conflict else ""}</td></tr>')
        unknown=[]
        known_places_by_year={}
        known_units_by_year_place={}
        for row in rows:
            year=safe_folder_component(row['record_year'])
            place=safe_folder_component(row['storage_name'])
            unit=safe_folder_component(row['unit_name'])
            known_places_by_year.setdefault(year,set()).add(place)
            known_units_by_year_place.setdefault((year,place),set()).add(unit)
        if os.path.isdir(root):
            try:
                for year_name in sorted(os.listdir(root),key=natural_key):
                    year_path=os.path.join(root,year_name)
                    if year_name not in known_years:
                        unknown.append((year_name,'مجلد غير معروف' if os.path.isdir(year_path) else 'ملف غير معروف'))
                        continue
                    if not os.path.isdir(year_path):
                        continue
                    for place_name in sorted(os.listdir(year_path),key=natural_key):
                        place_path=os.path.join(year_path,place_name)
                        if place_name not in known_places_by_year.get(year_name,set()):
                            unknown.append((year_name+'/'+place_name,'مكان تخزين غير معروف' if os.path.isdir(place_path) else 'ملف غير معروف'))
                            continue
                        if not os.path.isdir(place_path):
                            continue
                        for unit_name in sorted(os.listdir(place_path),key=natural_key):
                            unit_path=os.path.join(place_path,unit_name)
                            if unit_name not in known_units_by_year_place.get((year_name,place_name),set()):
                                unknown.append((year_name+'/'+place_name+'/'+unit_name,'وحدة غير معروفة' if os.path.isdir(unit_path) else 'ملف غير معروف'))
            except Exception:
                unknown.append(('—','تعذر قراءة بعض محتويات المسار'))
        unknown_html=''.join(f'<li><code dir="ltr">{esc(name)}</code> — {esc(kind)}</li>' for name,kind in unknown) or '<li>لا توجد مجلدات أو ملفات غير معروفة ضمن مستويات السنة ومكان التخزين والوحدة.</li>'
        unused_html=''.join(f'<li>{esc(row["storage_name"] or "غير محدد")} — {esc(row["unit_name"] or "غير محدد")}: لا توجد سجلات تحدد السنة؛ لم تُنشأ مجلدات لهذه الوحدة.</li>' for row in units_without_records) or '<li>كل الوحدات المعروفة مرتبطة بسجلات لها سنة محددة.</li>'
        rows_html=''.join(row_html) or '<tr><td colspan="5">لا توجد سجلات مرتبطة بسنوات ووحدات؛ لم تُنشأ أي مجلدات.</td></tr>'
        missing_count=sum(1 for _,state,conflict,_ in expected if state=='مفقود')
        conflict_count=sum(1 for _,state,conflict,_ in expected if state=='تعارض')
        content=f'''<div class="card"><h2>معاينة هيكل مجلدات الأرشيف</h2><p><a class="btn btn-primary" href="/storage/import-preview">استكشاف السنوات واستيراد الملفات والسجلات الناقصة</a></p>
<p>المسار: <code dir="ltr">{esc(root)}</code></p>
<p>هذه معاينة فقط؛ لم تُنشأ أو تُنقل أو تُحذف أي ملفات. تتم مطابقة السنوات والوحدات من قاعدة البيانات، ولا يُفترض أن كل مجلد موجود هو وحدة.</p>
<div class="stats-grid"><div class="stat-card"><h3>مسارات وارد/صادر مفقودة</h3><div class="num">{missing_count}</div></div><div class="stat-card"><h3>تعارضات مع ملفات</h3><div class="num">{conflict_count}</div></div><div class="stat-card"><h3>مجلدات/ملفات غير معروفة</h3><div class="num">{len(unknown)}</div></div></div>
</div><div class="card"><h3>الوحدات المعروفة من قاعدة البيانات</h3><div class="table-responsive"><table><tr><th>السنة</th><th>مكان التخزين</th><th>الوحدة</th><th>وارد</th><th>صادر</th></tr>{rows_html}</table></div></div>
<div class="card"><h3>عناصر غير معروفة — لن تُغيّر تلقائيًا</h3><ul>{unknown_html}</ul></div>
<div class="card"><h3>وحدات معروفة دون سجلات</h3><ul>{unused_html}</ul></div>
<div class="card"><h3>إنشاء المجلدات الناقصة فقط</h3><p>سيُنشئ البرنامج المسارات الناقصة للوحدات والسنوات المعروفة فقط. لن يغيّر الملفات الموجودة، ولن يحذف أو يعيد تسمية أي عنصر غير معروف، ولن يتجاوز أي تعارض.</p>
<form method="POST" action="/storage/structure/create" onsubmit="return confirm('هل تريد إنشاء المجلدات الناقصة التي ظهرت في المعاينة فقط؟');"><label class="check-row"><input type="checkbox" name="confirm_create" value="1" required> أوافق على إنشاء المجلدات الناقصة الموضحة أعلاه فقط.</label><button class="btn btn-success">إنشاء المجلدات الناقصة</button></form></div>'''
        self.sendhtml(base('معاينة هيكل المجلدات',content,'settings',err,ok,uid))


    

    def pdf_linker_page(self,uid,err='',ok=''):
        c=db()
        rows=c.execute('''SELECT a.id,a.record_id,a.original_name,a.relative_path,a.size_bytes,
                          r.direction,r.record_number,r.record_year,r.record_date,r.subject,
                          u.name AS unit_name,s.name AS storage_name
                          FROM attachments a JOIN records r ON r.id=a.record_id
                          LEFT JOIN units u ON u.id=r.unit_id
                          LEFT JOIN storage_places s ON s.id=u.storage_place_id
                          WHERE lower(a.original_name) LIKE '%.pdf'
                          ORDER BY a.id DESC LIMIT 500''').fetchall()
        c.close()
        s=settings()
        tr=[]
        for r in rows:
            direction='وارد' if r['direction']=='incoming' else 'صادر'
            tr.append('<tr><td>'+esc(r['original_name'])+'</td><td>'+direction+'</td><td>'+esc(r['record_number'])+'/'+esc(r['record_year'])+'</td><td>'+esc(r['unit_name'] or '')+'</td><td>'+esc(r['storage_name'] or '')+'</td><td><form method="POST" action="/attachments/unlink_safe" onsubmit="return confirm(\'سيُحذف الربط من قاعدة البيانات فقط، ولن يُحذف ملف PDF أو سجل الأرشيف. متابعة؟\')"><input type="hidden" name="attachment_id" value="'+str(r['id'])+'"><button class="btn btn-sm btn-warning">فك الربط</button></form></td></tr>')
        table='<table><thead><tr><th>اسم PDF</th><th>الاتجاه</th><th>رقم السجل</th><th>الوحدة</th><th>التخزين</th><th>الإجراء</th></tr></thead><tbody>'+''.join(tr or [])+'</tbody></table>' if tr else '<p class="muted">لا توجد روابط PDF مسجلة حتى الآن.</p>'
        content=f'''<div class="card"><h2>🔗 ربط ملفات PDF بالمراسلات</h2>
<p>تتم المطابقة من اسم الملف فقط؛ لا يقرأ البرنامج محتوى PDF ولا يستخدم OCR. راجع النتائج ثم أكد الربط صراحةً. الملفات الأصلية لا تُنقل ولا تُحذف.</p>
<form method="POST" action="/pdf-linker/match"><div class="form-group"><label>مجلد ملفات PDF المحلي</label><input name="folder_path" class="form-control" dir="ltr" value="{esc(s.get('pdf_linker_folder_path',''))}" placeholder="D:\\ArchivePDF" required></div><button class="btn btn-primary">فحص الأسماء وعرض المعاينة</button></form>
<p class="muted">المطابقة من اسم الملف فقط، عبر جميع السنوات والوحدات. لا يُقرأ محتوى PDF ولا يُستخدم OCR. تُعرض الحالات الملتبسة دون تخمين. لا يحدث أي ربط نهائي قبل التأكيد الصريح.</p></div>
<div class="card"><h3>الروابط الحالية</h3>{table}<p class="muted">© Noureddine 2026</p></div>'''
        self.sendhtml(base('ربط ملفات PDF',content,'pdf-linker',err,ok,uid))

    def templates_page(self,uid,f,s,err,ok,q=None):
        self.redir('/settings#templates'); return

    def security_page(self,uid,f,s,err,ok):
        if not self.admin(self.user()):
            self.redir('/settings?error='+urllib.parse.quote('هذه الصفحة متاحة للمدير فقط.')); return
        x=db(); users=x.execute('SELECT id,username,role FROM users ORDER BY username').fetchall(); logs=x.execute('SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT 30').fetchall(); x.close()
        usershtml=''.join(f'<tr><td>{esc(z["username"])}</td><td>{"مدير" if z["role"]=="admin" else "إضافة وتصفح"}</td><td><form method="POST" action="/users/delete" style="display:inline"><input type="hidden" name="id" value="{z["id"]}"><button class="btn btn-sm btn-danger" onclick="return confirm(\'هل تريد حذف هذا المستخدم؟\')">حذف</button></form></td></tr>' for z in users) or '<tr><td colspan="3">لا يوجد مستخدمون.</td></tr>'
        logshtml=''.join(f'<tr><td>{esc(l["timestamp"])}</td><td>{esc(l["action"])}</td><td>{esc(l["item_type"])} {esc(l["item_id"])}</td><td>{esc(l["details"])}</td></tr>' for l in logs) or '<tr><td colspan="4">لا توجد عمليات.</td></tr>'
        c=f'''<div class="card"><div class="flex-space"><div><h2>🛡️ الأمان والحماية</h2><p class="muted">مركز موحد لإعدادات وحدات التخزين المحلية والمستخدمين والصلاحيات والنسخ الاحتياطي وسجل الأمان.</p></div><a class="btn" href="/settings">↩️ العودة إلى الإعدادات</a></div></div>
<div class="card"><h3>🛡️ خيارات الحماية والأمان المستقلة</h3><p class="muted">البرنامج يعمل محلياً بالكامل. يمكنك تفعيل المراقبة المستقلة لوسائط USB والشبكة المحلية والإنترنت لحماية سرية الأرشيف.</p><form method="POST" action="/settings/security"><label class="check-row" style="margin-bottom:12px;display:flex;align-items:center;gap:10px;"><input type="checkbox" name="security_usb_warning" {"checked" if s.get("security_usb_warning","1")=="1" else ""}> <b>مراقبة وسائط التخزين (USB):</b> إنذار صوتي قوي ورسالة تحذير مع خيار متابعة العمل أو إغلاق البرنامج عند إدخال وحدة جديدة.</label><label class="check-row" style="margin-bottom:12px;display:flex;align-items:center;gap:10px;"><input type="checkbox" name="security_local_network_warning" {"checked" if s.get("security_local_network_warning","0")=="1" else ""}> <b>مراقبة الشبكة المحلية (LAN):</b> إنذار صوتي قوي ورسالة تحذير مع خيار متابعة العمل أو إغلاق البرنامج عند اكتشاف اتصال بالشبكة.</label><label class="check-row" style="margin-bottom:16px;display:flex;align-items:center;gap:10px;"><input type="checkbox" name="security_internet_shutdown" {"checked" if s.get("security_internet_shutdown","0")=="1" else ""}> <b>مراقبة الإنترنت:</b> إغلاق فوري تام للبرنامج وجلسة Chrome التابعة له بمجرد اكتشاف اتصال فعلي بالإنترنت (دون عد تنازلي أو خيار متابعة).</label><button class="btn btn-success">حفظ إعدادات الحماية</button></form></div>
<div class="card"><h3>🔐 المستخدمون والصلاحيات</h3><form method="POST" action="/users/add" class="search-box"><input name="username" placeholder="اسم المستخدم" required><input name="password" type="password" placeholder="كلمة المرور" required><select name="role"><option value="entry">مستخدم عادي</option><option value="admin">مدير</option></select><button class="btn btn-primary">إضافة مستخدم</button></form><table><tr><th>المستخدم</th><th>الصلاحية</th><th>إجراء</th></tr>{usershtml}</table><hr><h3>تغيير كلمة مرور حسابك</h3><form method="POST" action="/settings/password"><input name="new_password" type="password" class="form-control" required><button class="btn btn-danger">تغيير كلمة المرور</button></form></div>
<div class="card"><h3>💾 النسخ الاحتياطي والاستعادة</h3><a class="btn btn-success" href="/backup">💾 إنشاء نسخة احتياطية</a><form method="POST" action="/restore" enctype="multipart/form-data" class="search-box" style="margin-top:12px" onsubmit="return confirm('سيتم حفظ نسخة من البيانات الحالية ثم استبدالها بمحتوى النسخة الاحتياطية. هل تريد المتابعة؟')"><input type="file" name="file" accept=".zip,application/zip" required><button class="btn btn-danger">♻️ استعادة نسخة احتياطية</button></form></div>
<div class="card"><h3>📋 سجل الأمان والعمليات — آخر 30 عملية</h3><div class="table-responsive"><table><tr><th>التاريخ</th><th>العملية</th><th>العنصر</th><th>التفاصيل</th></tr>{logshtml}</table></div></div>'''
        self.sendhtml(base('الأمان والحماية',c,'settings',err,ok,uid))

    def settings_page(self,uid,f,s,err,ok,q=None):
        q=q or urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        x=db(); sps=x.execute('SELECT * FROM storage_places ORDER BY name').fetchall(); units=x.execute('SELECT u.*,s.name sp_name FROM units u LEFT JOIN storage_places s ON s.id=u.storage_place_id ORDER BY s.name,u.name').fetchall(); users=x.execute('SELECT id,username,role FROM users ORDER BY username').fetchall(); logs=x.execute('SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT 20').fetchall()
        x.close()
        sps_html=''.join(f'<tr><td><form method="POST" action="/structure/edit-storage" class="search-box"><input type="hidden" name="id" value="{m["id"]}"><input name="name" class="form-control" value="{esc(m["name"])}" required><button class="btn btn-sm btn-success">حفظ</button></form></td><td><form method="POST" action="/storage/delete" style="display:inline"><input type="hidden" name="id" value="{m["id"]}"><button class="btn btn-sm btn-danger" onclick="return confirm({json.dumps(s["confirm_delete"],ensure_ascii=False)})">حذف</button></form></td></tr>' for m in sps) or '<tr><td colspan="2">لا توجد أماكن تخزين.</td></tr>'
        unit_rows=[]
        for u in units:
            opts=''.join('<option value="{}" {}>{}</option>'.format(m['id'], 'selected' if str(u['storage_place_id'] or '') == str(m['id']) else '', esc(m['name'])) for m in sps)
            unit_rows.append(f'<tr><td><form method="POST" action="/structure/edit-unit" class="search-box"><input type="hidden" name="id" value="{u["id"]}"><input name="name" class="form-control" value="{esc(u["name"])}" required><select name="storage_place_id" class="form-control" required>{opts}</select><button class="btn btn-sm btn-success">حفظ ونقل</button></form></td><td>📍 {esc(u["sp_name"] or "—")}</td><td><form method="POST" action="/units/delete" style="display:inline"><input type="hidden" name="id" value="{u["id"]}"><button class="btn btn-sm btn-danger" onclick="return confirm({json.dumps(s["confirm_delete"],ensure_ascii=False)})">حذف</button></form></td></tr>')
        units_html=''.join(unit_rows) or '<tr><td colspan="3">لا توجد وحدات.</td></tr>'
        unitopts_all='<option value="">اختر الوحدة</option>'+''.join(f'<option value="{u["id"]}">{esc(u["name"])}</option>' for u in units)
        spopts=''.join(f'<option value="{m["id"]}">{esc(m["name"])}</option>' for m in sps)
        syskeys=['system_name','incoming_title','outgoing_title','quick_title','search_placeholder','unit_prompt','location_help','number_help','subject_help','date_help','notes_help','confirm_delete','print_header']
        settingsform=''.join((f'<div class="form-group"><label>{esc(k)}</label><input type="color" name="{k}" class="form-control" value="{esc(s[k])}"></div>' if k=='login_marquee_color' else f'<div class="form-group"><label>{esc(k)}</label><input name="{k}" class="form-control" value="{esc(s.get(k,""))}"></div>') for k in syskeys)
        flags=['location_help','number_help','subject_help','date_help','notes_help']; helpflags=''.join(f'<label class="check-row"><input type="checkbox" name="show_{k}" {"checked" if s.get("show_"+k)=="1" else ""}> إظهار التوجيه: {esc(s.get(k,""))}</label>' for k in flags)
        fieldsform=''.join(f'<div class="form-group"><label>{esc(DEFAULT_FIELDS[k])}</label><input name="{k}" class="form-control" value="{esc(f.get(k,""))}"></div>' for k in DEFAULT_FIELDS)
        usershtml=''.join(f'<tr><td>{esc(z["username"])}</td><td>{"مدير" if z["role"]=="admin" else "إضافة وتصفح"}</td><td><form method="POST" action="/users/delete" style="display:inline"><input type="hidden" name="id" value="{z["id"]}"><button class="btn btn-sm btn-danger" onclick="return confirm(\'هل تريد حذف هذا المستخدم؟\')">حذف</button></form></td></tr>' for z in users); logshtml=''.join(f'<tr><td>{esc(l["timestamp"])}</td><td>{esc(l["action"])}</td><td>{esc(l["item_type"])} {esc(l["item_id"])}</td><td>{esc(l["details"])}</td></tr>' for l in logs)

        archive_root=resolve_archive_root(q.get('picked_archive_root',[''])[0] or s.get('archive_root_path','documents'))
        archive_migrate_url='/storage/migrate-preview?new_root='+urllib.parse.quote(archive_root,safe='')
        archive_root_ui=f'''<div class="card"><h2>🗂️ مكان التخزين الرئيسي للأرشيف</h2>
<p class="muted">كل النسخ الرقمية الجديدة تُحفظ ضمن هذا المسار وفق السنة ثم مكان التخزين ثم الوحدة ثم وارد/صادر. لا يُستخدم هذا المسار لنماذج المراسلات.</p>
<form method="POST" action="/storage/root" class="search-box">
<input type="text" name="root_path" class="form-control" value="{esc(archive_root)}" required>
<button class="btn btn-primary" name="action" value="test">اختبار المسار</button>
<button class="btn btn-success" name="action" value="save">حفظ المسار</button>
<a class="btn" href="/storage/pick-root">استعراض المجلدات…</a>
<a class="btn" href="/storage/structure">معاينة هيكل المجلدات</a>
<a class="btn" href="{esc(archive_migrate_url)}">معاينة تنظيم المرفقات الموجودة</a>
</form>
<div style="margin-top:15px;padding-top:12px;border-top:1px dashed #dcdde1;">
<form method="POST" action="/storage/smart-sync" style="display:inline;"><button class="btn btn-primary" onclick="return confirm('تنفيذ التحديث الذكي الآن؟ سيتم استيعاب جميع السنوات والمخازن والوحدات المكتملة وربط ملفات PDF تلقائيًا دون تكرار.');">🔄 تنفيذ التحديث الذكي للأرشيف</button></form>
 <span class="muted" style="margin-right:10px;">يستوعب السنوات والمخازن والوحدات والملفات وينشئ السجلات الناقصة مع منع التكرار.</span>
</div></div>'''
                pdf_linker_settings_ui=f'''<div class="card" id="pdf-linker-settings"><h2>🔗 ربط ملفات PDF بالمراسلات</h2>
<p class="muted">افحص أسماء ملفات PDF واعرض المطابقات عبر السنوات والوحدات قبل إنشاء أي رابط. لا يقرأ البرنامج محتوى الملفات، ولا ينقلها أو يحذفها، ولا يعمل الربط تلقائيًا.</p>
<a class="btn btn-primary" href="/pdf-linker">فتح أداة ربط PDF ومعاينة المطابقات</a></div>'''
        x_t=db()
        t_rows=x_t.execute('SELECT t.*, u.name as unit_name, s.name as sp_name FROM templates t LEFT JOIN units u ON u.id=t.unit_id LEFT JOIN storage_places s ON s.id=t.storage_place_id ORDER BY t.name').fetchall()
        x_t.close()
        t_sp_opts='<option value="">عام / كل أماكن التخزين</option>'+''.join(f'<option value="{m["id"]}">{esc(m["name"])}</option>' for m in sps)
        t_u_opts='<option value="">عام / كل الوحدات</option>'+''.join(f'<option value="{u["id"]}">{esc(u["name"])} ({esc(u["sp_name"] or "—")})</option>' for u in units)
        t_table_rows=''.join(f'<tr><td><b>{esc(t["name"])}</b></td><td>{str(t["year"]) if t["year"] else "عام"}</td><td>{esc(t["sp_name"] or "عام")}</td><td>{esc(t["unit_name"] or "عام")}</td><td><code dir="ltr">{esc(t["file_path"])}</code></td><td><a class="btn btn-sm btn-primary" href="/templates/download?id={t["id"]}">📥 تحميل / فتح</a> <form method="POST" action="/settings/templates/delete" style="display:inline"><input type="hidden" name="id" value="{t["id"]}"><button class="btn btn-sm btn-danger" onclick="return confirm(\'هل تريد حذف مرجع هذا النموذج؟ لن يتم حذف الملف الأصلي من القرص.\')">حذف المرجع</button></form></td></tr>' for t in t_rows) or '<tr><td colspan="6" class="text-center">لا توجد نماذج مضافة حالياً. يمكنك إضافة نموذج من النموذج أدناه.</td></tr>'
        templates_settings_ui=f'''<div class="card" id="templates"><h2>📝 إدارة نماذج المراسلات (Word)</h2>
<p class="muted">تُدار نماذج المراسلات هنا وترتبط بالسنة والمخزن والوحدة، وتظهر تلقائيًا في شاشات الصادر والوارد للوحدة نفسها. حذف المرجع لا يحذف الملف الأصلي.</p>
<form method="POST" action="/settings/templates/upload" enctype="multipart/form-data"><input type="hidden" name="csrf_token" value="{csrf(self)}">
<div class="settings-grid">
<div class="form-group"><label>اسم النموذج</label><input name="name" class="form-control" placeholder="مثال: نموذج مذكرة إحالة" required></div>
<div class="form-group"><label>السنة (اختياري)</label><input name="year" class="form-control" placeholder="مثال: 2026"></div>
<div class="form-group"><label>مكان التخزين (المخزن)</label><select name="storage_place_id" class="form-control">{t_sp_opts}</select></div>
<div class="form-group"><label>الوحدة</label><select name="unit_id" class="form-control">{t_u_opts}</select></div>
<div class="form-group"><label>ملف Word (.doc, .docx)</label><input type="file" name="file" class="form-control" accept=".doc,.docx,.dotx" required></div>
</div>
<button class="btn btn-success" type="submit">➕ حفظ النموذج وإضافته</button>
</form>
<hr style="margin:20px 0;border:0;border-top:1px solid #e1e8ed;">
<h3>النماذج المسجلة في النظام</h3>
<div class="table-responsive"><table><thead><tr><th>اسم النموذج</th><th>السنة</th><th>المخزن</th><th>الوحدة</th><th>الملف</th><th>إجراءات</th></tr></thead><tbody>{t_table_rows}</tbody></table></div>
</div>'''
        login_ui=f'''<details class="card"><summary style="cursor:pointer;font-weight:bold;font-size:16px;padding:6px 0;">🎨 تخصيص واجهة الدخول — اضغط للفتح</summary><div style="margin-top:14px"><h2>🎨 تخصيص واجهة الدخول</h2>
<p class="muted">عدّل النصوص وشاهد المعاينة فورًا، ثم احفظ. لا حاجة للخروج أو تسجيل الدخول مجددًا. البصمة ثابتة ولا يمكن تخصيصها.</p>
<form method="POST" action="/settings/login-ui" id="loginCustomizationForm">
<div class="settings-grid">
<div class="form-group"><label>اسم الجهة الظاهر</label><input id="login_org_input" name="organization_name" class="form-control" value="{esc(s.get('organization_name','Digital Archive Pro'))}" required></div>
<div class="form-group"><label>عنوان واجهة الدخول</label><input id="login_title_input" name="login_title" class="form-control" value="{esc(s.get('login_title','Digital Archive Pro'))}" required></div>
<div class="form-group"><label>رسالة الترحيب</label><input id="login_welcome_input" name="welcome_message" class="form-control" value="{esc(s.get('welcome_message','مرحبًا بكم في Digital Archive Pro'))}" required></div>
<div class="form-group"><label>النص المتحرك</label><input id="login_marquee_input" name="login_marquee" class="form-control" value="{esc(s.get('login_marquee','Digital Archive Pro — مرحبًا بكم'))}"></div>
<div class="form-group"><label>لون النص المتحرك</label><input id="login_color_input" name="login_marquee_color" type="color" class="form-control" value="{esc(s.get('login_marquee_color','#16a085'))}"></div>
</div>
<div class="login-preview" style="margin:18px auto;padding:22px;border:1px solid #d7e2dc;border-radius:14px;max-width:620px;background:#f8fbf9;text-align:center">
<div id="login_preview_org" style="font-size:27px;font-weight:900">{esc(s.get('organization_name','Digital Archive Pro'))}</div>
<div id="login_preview_title" style="font-size:17px;margin:8px 0">{esc(s.get('login_title','Digital Archive Pro'))} — Digital Archive Pro</div>
<div id="login_preview_welcome" style="margin:10px 0">{esc(s.get('welcome_message','مرحبًا بكم في Digital Archive Pro'))}</div>
<div id="login_preview_marquee" style="font-weight:900;font-size:18px;color:{esc(s.get('login_marquee_color','#16a085'))}">{esc(s.get('login_marquee','Digital Archive Pro — مرحبًا بكم'))}</div>
<div style="margin-top:16px;font-size:11px;color:#68766f;direction:ltr">© Noureddine 2026</div>
</div>
<button class="btn btn-success">حفظ وتطبيق التغييرات</button>
</form>
<script>
(function(){{
 const fields=[['login_org_input','login_preview_org'],['login_welcome_input','login_preview_welcome'],['login_marquee_input','login_preview_marquee']];
 fields.forEach(function(pair){{const input=document.getElementById(pair[0]),target=document.getElementById(pair[1]);input.addEventListener('input',function(){{target.textContent=input.value;}});}});
 const title=document.getElementById('login_title_input');
 title.addEventListener('input',function(){{document.getElementById('login_preview_title').textContent=title.value+' — Digital Archive Pro';}});
 const color=document.getElementById('login_color_input');
 color.addEventListener('input',function(){{document.getElementById('login_preview_marquee').style.color=color.value;}});
}})();
</script></div></div></details>'''
        reset_ui=f'''<details class="card"><summary style="cursor:pointer;font-weight:bold;font-size:16px;padding:6px 0;">♻️ خيارات إعادة الضبط وحذف البيانات — اضغط للفتح</summary><div style="margin-top:14px"><h2>إعادة الضبط</h2>
<div class="reset-panel"><h3>إعادة ضبط الإعدادات فقط</h3><p>إعادة النصوص والحقول وخيارات الواجهة إلى القيم الافتراضية. لن تُحذف السجلات أو الوحدات أو الملفات أو المستخدمون أو اختصارات الفتح السريع، وسيُحافظ على مساري الأرشيف والنماذج.</p>
<form method="POST" action="/reset/settings" onsubmit="return confirm('هل تريد إعادة ضبط إعدادات الواجهة فقط؟ لن يُحذف الأرشيف.');"><div class="form-group"><label>كلمة مرور المدير للتأكيد</label><input type="password" name="admin_password" class="form-control" autocomplete="current-password" required></div><button class="btn btn-warning">إعادة ضبط الإعدادات فقط</button></form></div>
<div class="reset-panel reset-danger"><h3>إعادة ضبط الأرشيف بالكامل</h3>
<p>سيتم حذف السجلات والروابط والنسخ الرقمية والوحدات وأماكن التخزين، ثم حذف مجلد الأرشيف النشط. لن يُحذف البرنامج أو حسابات المستخدمين أو مجلد نماذج المراسلات. النسخ الاحتياطية السابقة الموجودة خارج المسار لن تُحذف تلقائيًا.</p>
<p><strong>المسار الذي سيُحذف محتواه:</strong> <code dir="ltr">{esc(archive_root)}</code></p>
<form method="POST" action="/reset/archive" onsubmit="return confirm('تحذير نهائي: سيتم حذف الأرشيف الرقمي ومجلداته. هل راجعت المسار وأخذت نسخة احتياطية عند الحاجة؟');">
<div class="form-group"><label>اكتب المسار أعلاه كما هو لتأكيد الحذف</label><input name="confirm_path" class="form-control" required autocomplete="off" placeholder="أعد كتابة مسار الأرشيف"></div>
<div class="form-group"><label>كلمة مرور المدير للتأكيد</label><input type="password" name="admin_password" class="form-control" autocomplete="current-password" required></div>
<label class="check-row"><input type="checkbox" name="confirm_reset" value="1" required> أفهم أن هذا الإجراء سيحذف الأرشيف الرقمي ولا يمكن التراجع عنه من داخل البرنامج.</label>
<button class="btn btn-danger">حذف الأرشيف بالكامل</button></form></div></div></div></details>'''
        c=f'''{archive_root_ui}{pdf_linker_settings_ui}{templates_settings_ui}{login_ui}{reset_ui}<details class="card"><summary style="cursor:pointer;font-weight:bold;font-size:16px;padding:6px 0;">🏛️ إدارة الهيكل التنظيمي (أماكن التخزين والوحدات) — اضغط للفتح</summary><div style="margin-top:14px"><h2>إدارة الهيكل التنظيمي</h2><p.</p><form method="POST" action="/structure/add" class="structure-add"><div class="form-group"><label>نوع العنصر</label><select id="entity_type" name="entity_type" class="form-control" onchange="toggleParent()"><option value="storage">مكان تخزين</option><option value="unit">وحدة</option></select></div><div class="form-group"><label id="name_label">اسم مكان التخزين</label><input name="name" id="name_input" class="form-control" required></div><div class="form-group" id="storage_group" style="display:none"><label>مكان التخزين التابعة له الوحدة</label><select name="storage_place_id" class="form-control"><option value="">اختر مكان التخزين</option>{spopts}</select></div><div class="form-group structure-submit"><button class="btn btn-primary">إضافة</button></div></form><div class="structure-grid"><div><h3>أماكن التخزين</h3><table><tr><th>مكان التخزين</th><th>إجراء</th></tr>{sps_html}</table></div><div><h3>الوحدات</h3><table><tr><th>الوحدة ومكان التخزين التابعة له</th><th>مكان التخزين</th><th>إجراء</th></tr>{units_html}</table></div></div></div><script>function toggleParent(){{const t=document.getElementById('entity_type').value;document.getElementById('storage_group').style.display=t==='unit'?'block':'none';document.getElementById('name_label').textContent=t==='unit'?'اسم الوحدة':'اسم مكان التخزين';}}</script>
                <div class="card"><h2>🛡️ خيارات الحماية والأمان</h2>
<p class="muted">البرنامج يعمل محلياً بالكامل. يمكنك تفعيل المراقبة المستقلة لوسائط USB والشبكة المحلية والإنترنت لحماية سرية الأرشيف.</p>
<form method="POST" action="/settings/security">
<label class="check-row" style="margin-bottom:12px;display:flex;align-items:center;gap:10px;"><input type="checkbox" name="security_usb_warning" {"checked" if s.get("security_usb_warning","1")=="1" else ""}> <b>مراقبة وسائط التخزين (USB):</b> إنذار صوتي قوي ورسالة تحذير مع خيار متابعة العمل أو إغلاق البرنامج عند إدخال وحدة جديدة.</label>
<label class="check-row" style="margin-bottom:12px;display:flex;align-items:center;gap:10px;"><input type="checkbox" name="security_local_network_warning" {"checked" if s.get("security_local_network_warning","0")=="1" else ""}> <b>مراقبة الشبكة المحلية (LAN):</b> إنذار صوتي قوي ورسالة تحذير مع خيار متابعة العمل أو إغلاق البرنامج عند اكتشاف اتصال بالشبكة.</label>
<label class="check-row" style="margin-bottom:16px;display:flex;align-items:center;gap:10px;"><input type="checkbox" name="security_internet_shutdown" {"checked" if s.get("security_internet_shutdown","0")=="1" else ""}> <b>مراقبة الإنترنت:</b> إغلاق فوري تام للبرنامج وجلسة Chrome التابعة له بمجرد اكتشاف اتصال فعلي بالإنترنت (دون عد تنازلي أو خيار متابعة).</label>
<button class="btn btn-success" type="submit">حفظ إعدادات الحماية</button> <a class="btn" href="/security">إدارة المستخدمين وسجل الأمان</a>
</form></div><details class="card"><summary><b>⚙️ إعدادات النظام والنصوص</b> — اضغط للفتح</summary><div style="margin-top:14px"><h2>إعدادات النظام والنصوص</h2><p class="muted">يمكن تعديل جميع العبارات الظاهرة تقريباً، أو إخفاء نصوص المساعدة.</p><form method="POST" action="/settings/texts"><div class="settings-grid">{settingsform}</div><h3>إظهار أو إخفاء التوجيهات</h3><div class="help-grid">{helpflags}</div><button class="btn btn-success">حفظ إعدادات النظام</button></form></div></details><details class="card"><summary><b>🏷️ أسماء الحقول الظاهرة</b> — اضغط للفتح</summary><div style="margin-top:14px"><h2>أسماء الحقول الظاهرة</h2><form method="POST" action="/settings/fields"><div class="settings-grid">{fieldsform}</div><button class="btn btn-success">حفظ أسماء الحقول</button></form></div></details><details class="card"><summary style="cursor:pointer;font-weight:bold;font-size:16px;padding:6px 0;">📋 سجل العمليات (آخر 20 عملية) — اضغط للفتح</summary><div style="margin-top:14px"><h2>سجل العمليات - آخر 20 عملية</h2><div class="table-responsive"><table><tr><th>التاريخ</th><th>العملية</th><th>العنصر</th><th>التفاصيل</th></tr>{logshtml}</table></div></div></details>'''
        self.sendhtml(base('الإعدادات',c,'settings',err,ok,uid))

    def print_page(self,path,q):
        u=self.user()
        if not u:self.redir('/login');return
        f=fields(); s=settings(); x=db(); header=q.get('header',[''])[0] or s['print_header'] or s['system_name']
        col_defaults=['direction','number','reference','subject','date','unit','location','notes','created']
        cols=q.get('col',[]) or col_defaults
        cols=[c for c in col_defaults if c in cols]
        labels={'direction':'النوع','number':'الرقم','reference':'المرجع','subject':'الموضوع','date':'التاريخ','unit':'الوحدة','location':'مكان الحفظ','notes':'الملاحظات','created':'تاريخ الإضافة'}
        if path=='/print/card':
            rid=q.get('id',[''])[0]; r=x.execute('SELECT r.*,u.name unit_name FROM records r LEFT JOIN units u ON u.id=r.unit_id WHERE r.id=?',(rid,)).fetchone()
            if not r:x.close();self.send_error(404);return
            linked=x.execute('SELECT r.* FROM record_links l JOIN records r ON r.id=(CASE WHEN l.incoming_id=? THEN l.outgoing_id ELSE l.incoming_id END) WHERE l.incoming_id=? OR l.outgoing_id=? ORDER BY r.id',(rid,rid,rid)).fetchall()
            incoming=r['direction']=='incoming';
            dir_label = 'الوارد' if incoming else 'الصادر'
            title=f'بطاقة مراسلة — {dir_label}'
            number=f['incoming_number'] if incoming else f['outgoing_number']; subject=f['incoming_subject'] if incoming else f['outgoing_subject']; date=f['incoming_date'] if incoming else f['outgoing_date']; loc=f['physical_location'] if incoming else f['outgoing_location']
            linked_text='، '.join(('وارد ' if z['direction']=='incoming' else 'صادر ')+f"{z['record_number']}/{z['record_year']}" for z in linked) or 'لا توجد مراسلات مرتبطة'
            vals=[('الاتجاه',dir_label),('الرقم',f"{r['record_number']}/{r['record_year']}"),(f['reference'],r['reference'] or '—'),(subject,r['subject']),(date,r['record_date']),(f['unit'],r['unit_name'] or '—'),(loc,r['physical_location']),('المراسلات المرتبطة',linked_text)]
            rows_html=''.join(f'<tr><th>{esc(a)}</th><td>{esc(b)}</td></tr>' for a,b in vals); x.close()
            out=f'''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>{esc(title)}</title><style>body{{font-family:Arial,sans-serif;margin:0;padding:12mm;color:#111}}.card{{border:2px solid #222;border-radius:10px;padding:18px;max-width:850px;margin:auto}}h1{{text-align:center;margin:0 0 16px;font-size:22px}}table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid #555;padding:9px;text-align:right}}th{{width:30%;background:#eee}}.actions{{text-align:center;margin:15px}}@media print{{.actions{{display:none}}body{{padding:0}}@page{{size:A4 portrait;margin:12mm}}}}</style></head><body><div class="actions"><button onclick="window.print()">🖨️ طباعة البطاقة</button></div><div class="card"><h1>{esc(title)}</h1><table>{rows_html}</table><p style="margin-top:16px;font-size:12px">تم إنشاء البطاقة من Digital Archive Pro.</p></div></body></html>'''
            self.sendhtml(out); return

        if path=='/print/record':
            rid=q.get('id',[''])[0]; r=x.execute('SELECT r.*,u.name unit_name FROM records r LEFT JOIN units u ON u.id=r.unit_id WHERE r.id=?',(rid,)).fetchone()
            if not r:x.close();self.send_error(404);return
            rows=[r]; scope='الوحدة: '+(r['unit_name'] or '—'); title=s['incoming_title'] if r['direction']=='incoming' else s['outgoing_title']
        else:
            where,par,order_sql=self._report_query(q); rows=x.execute('SELECT r.*,u.name unit_name FROM records r LEFT JOIN units u ON u.id=r.unit_id '+where+' ORDER BY '+order_sql,par).fetchall(); scope=self._report_scope(q,x); title='طباعة الأرشيف'
        x.close()
        if not cols: cols=['number','subject','date']
        data=[]
        for r in rows:
            dir_label = "وارد" if r['direction']=='incoming' else "صادر"
            full_type = dir_label
            vals={'direction':full_type,'number':f"{r['record_number']}/{r['record_year']}",'reference':r['reference'] or '—','subject':r['subject'],'date':r['record_date'],'unit':r['unit_name'] or '—','location':r['physical_location'],'notes':r['notes'] or '—','created':r['created_at']}
            data.append(vals)
        th=''.join(f'<th>{esc(labels[c])}</th>' for c in cols)
        trs=''.join('<tr>'+''.join(f'<td>{esc(row[c])}</td>' for c in cols)+'</tr>' for row in data)
        # Return to report builder while preserving all selected options.
        back_params={k:v for k,v in q.items() if k not in ('header',)}
        back_params['header']=[header]
        back_q=urllib.parse.urlencode([(k,v) for k,vals in back_params.items() for v in vals])
        out=f'''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>{esc(header)}</title><style>body{{font-family:Arial,sans-serif;margin:30px;color:#111}}h1,h2{{text-align:center}}table{{width:100%;border-collapse:collapse;margin-top:20px;table-layout:auto}}th,td{{border:1px solid #555;padding:7px;text-align:right;vertical-align:top;word-break:break-word}}th{{background:#eee}}.actions{{text-align:center;margin:20px;display:flex;gap:8px;justify-content:center}}.actions a,.actions button{{padding:9px 16px;border:1px solid #555;background:#f5f5f5;text-decoration:none;color:#111;border-radius:5px}}@media print{{.actions{{display:none}}@page{{size:A4 landscape;margin:12mm}}body{{margin:0;font-size:11px}}th,td{{padding:5px}}}}</style></head><body><div class="actions"><button onclick="window.print()">🖨️ طباعة</button><a href="/reports?{esc(back_q)}">↩️ تعديل التقرير</a></div><h1>{esc(header)}</h1><h2>{esc(scope)}</h2><p style="text-align:center">عدد السجلات: {len(rows)} — الترتيب: {esc(q.get('sort',['number'])[0])} ({'تصاعدي' if q.get('order',['asc'])[0]=='asc' else 'تنازلي'})</p><table><tr>{th}</tr>{trs or '<tr><td colspan="%d" style="text-align:center">لا توجد سجلات.</td></tr>'%len(cols)}</table></body></html>'''
        self.sendhtml(out)

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path=='/security/status':
            self.send_error(405,'Method Not Allowed')
            return
        if self.client_address[0] not in ('127.0.0.1','::1'):
            self.send_error(403,'Localhost only')
            return
        p=urllib.parse.urlparse(self.path); path=p.path
        if path=='/restore':
            u=self.require()
            if not u:return
            if u['role']!='admin': self.redir('/?error='+urllib.parse.quote('المدير فقط يستطيع استعادة النسخة الاحتياطية.')); return
            form={}; temp=None; extract=None; staged_root=None; current_root=None; old_root=None; db_backup=None; root_moved=False; root_installed=False; db_replaced=False
            try:
                form,upload=self.multipart_file(); self.require_csrf(form)
                if not upload: raise ValueError('اختر ملف النسخة الاحتياطية ZIP.')
                _,original,mime,data=upload
                if not original.lower().endswith('.zip'): raise ValueError('ملف الاستعادة يجب أن يكون ZIP.')
                temp=os.path.join(BASE_DIR,'restore_'+uuid.uuid4().hex+'.zip')
                with open(temp,'wb') as out: out.write(data)
                extract=os.path.join(BASE_DIR,'_restore_tmp_'+uuid.uuid4().hex)
                os.makedirs(extract,exist_ok=True)
                with zipfile.ZipFile(temp,'r') as z:
                    names=z.namelist()
                    if 'archive.db' not in names: raise ValueError('النسخة الاحتياطية لا تحتوي على archive.db.')
                    for name in names:
                        normalized=name.replace('\\','/')
                        if normalized.startswith('/') or re.match(r'^[A-Za-z]:',normalized) or '..' in normalized.split('/'):
                            raise ValueError('ملف النسخة الاحتياطية يحتوي على مسار غير آمن.')
                    z.extractall(extract)
                restored_db=os.path.join(extract,'archive.db')
                try:
                    testdb=sqlite3.connect(restored_db)
                    integrity=testdb.execute('PRAGMA integrity_check').fetchone()[0]
                    fk=list(testdb.execute('PRAGMA foreign_key_check').fetchall())
                    testdb.close()
                except sqlite3.DatabaseError:
                    raise ValueError('قاعدة البيانات داخل النسخة الاحتياطية غير سليمة؛ لم تُستبدل البيانات الحالية.')
                if integrity!='ok' or fk:
                    raise ValueError('قاعدة البيانات داخل النسخة الاحتياطية غير سليمة؛ لم تُستبدل البيانات الحالية.')
                attachment_updates=[]
                restored_con=None
                try:
                    restored_con=sqlite3.connect(restored_db)
                    restored_con.row_factory=sqlite3.Row
                    old_root_row=restored_con.execute("SELECT value FROM settings WHERE key='archive_root_path'").fetchone()
                    old_root_setting=old_root_row['value'] if old_root_row else 'documents'
                    for attachment_row in restored_con.execute('SELECT id,relative_path FROM attachments').fetchall():
                        relative=relative_path_inside_archive(attachment_row['relative_path'],old_root_setting)
                        if relative is not None:
                            attachment_updates.append((attachment_row['id'],relative))
                except sqlite3.DatabaseError:
                    attachment_updates=[]
                finally:
                    if restored_con is not None: restored_con.close()
                current_root=resolve_archive_root()
                parent=os.path.dirname(current_root)
                if not os.path.isdir(parent):
                    raise ValueError('المجلد الأب لمسار الأرشيف غير موجود؛ أصلح المسار قبل الاستعادة.')
                source_files=os.path.join(extract,'archive_files')
                if not os.path.isdir(source_files):
                    legacy_files=os.path.join(extract,'documents')
                    source_files=legacy_files if os.path.isdir(legacy_files) else None
                staged_root=current_root+'.restore_tmp_'+uuid.uuid4().hex
                os.makedirs(staged_root,exist_ok=False)
                if source_files:
                    for root,dirs,files in os.walk(source_files):
                        rel_dir=os.path.relpath(root,source_files)
                        dest_dir=staged_root if rel_dir=='.' else os.path.join(staged_root,rel_dir)
                        os.makedirs(dest_dir,exist_ok=True)
                        for fn in files:
                            src=os.path.join(root,fn)
                            if os.path.islink(src):
                                raise ValueError('النسخة الاحتياطية تحتوي على رابط ملفات غير مسموح.')
                            shutil.copy2(src,os.path.join(dest_dir,fn))
                stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                if os.path.isfile(DB_FILE):
                    db_backup=os.path.join(BASE_DIR,'archive_pre_restore_'+stamp+'_'+uuid.uuid4().hex[:6]+'.db')
                    shutil.copy2(DB_FILE,db_backup)
                if os.path.isdir(current_root):
                    old_root=current_root+'.pre_restore_'+stamp
                    while os.path.exists(old_root):
                        old_root=current_root+'.pre_restore_'+stamp+'_'+uuid.uuid4().hex[:6]
                    os.rename(current_root,old_root)
                    root_moved=True
                os.rename(staged_root,current_root)
                staged_root=None; root_installed=True
                # Copy database contents through SQLite's backup API instead of
                # replacing an open file; Windows may deny os.replace on a live DB.
                db_replaced=True
                source_con=None; target_con=None
                try:
                    source_con=sqlite3.connect(restored_db)
                    target_con=sqlite3.connect(DB_FILE)
                    source_con.backup(target_con)
                    target_con.commit()
                finally:
                    if target_con is not None: target_con.close()
                    if source_con is not None: source_con.close()
                con=sqlite3.connect(DB_FILE)
                missing_attachment_paths=0
                try:
                    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('archive_root_path',?)",(stored_attachment_path(current_root),))
                    for attachment_id,relative in attachment_updates:
                        restored_file=os.path.join(current_root,relative)
                        if os.path.isfile(restored_file):
                            con.execute('UPDATE attachments SET relative_path=? WHERE id=?',(stored_attachment_path(restored_file),attachment_id))
                        else:
                            missing_attachment_paths+=1
                    con.commit()
                    ids=[row[0] for row in con.execute('SELECT id FROM records').fetchall()]
                finally:
                    con.close()
                for rid in ids:
                    try: ensure_record_archive_folders(rid)
                    except Exception: pass
                shutil.rmtree(extract,ignore_errors=True)
                if temp and os.path.isfile(temp): os.remove(temp)
                self.send_response(302); self.send_header('Location','/login?restored=1'); self.end_headers(); return
            except ValueError as e:
                try:
                    if db_replaced and db_backup and os.path.isfile(db_backup): shutil.copy2(db_backup,DB_FILE)
                    if root_installed and current_root and os.path.isdir(current_root): shutil.rmtree(current_root,ignore_errors=True)
                    if root_moved and old_root and os.path.isdir(old_root):
                        os.rename(old_root,current_root)
                    if staged_root and os.path.isdir(staged_root): shutil.rmtree(staged_root,ignore_errors=True)
                    if temp and os.path.isfile(temp): os.remove(temp)
                    if extract: shutil.rmtree(extract,ignore_errors=True)
                except Exception: pass
                self.redir('/settings?error='+urllib.parse.quote(str(e))); return
            except Exception as e:
                try:
                    if db_replaced and db_backup and os.path.isfile(db_backup): shutil.copy2(db_backup,DB_FILE)
                    if root_installed and current_root and os.path.isdir(current_root): shutil.rmtree(current_root,ignore_errors=True)
                    if root_moved and old_root and os.path.isdir(old_root):
                        os.rename(old_root,current_root)
                    if staged_root and os.path.isdir(staged_root): shutil.rmtree(staged_root,ignore_errors=True)
                    if temp and os.path.isfile(temp): os.remove(temp)
                    if extract: shutil.rmtree(extract,ignore_errors=True)
                except Exception: pass
                self.redir('/settings?error='+urllib.parse.quote('تعذر استعادة النسخة: '+str(e))); return
        if path=='/attachments/add':
            u=self.require()
            if not u:return
            uid=u['id']; form={}
            try:
                form,upload=self.multipart_file(); self.require_csrf(form); rid=form.get('record_id','')
                if not upload: raise ValueError('اختر ملفًا لإرفاقه.')
                x=db(); r=x.execute('SELECT id,direction FROM records WHERE id=?',(rid,)).fetchone(); x.close()
                if not r: raise ValueError('السجل غير موجود.')
                _,original,mime,data=upload; ext=os.path.splitext(original)[1].lower()
                allowed={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.pdf':'application/pdf'}
                if ext not in allowed: raise ValueError('الملفات المسموح بها: JPG وJPEG وPNG وPDF فقط.')
                mime=allowed[ext]; folder=ensure_record_archive_folders(r['id'])
                stored=uuid.uuid4().hex+ext; full=os.path.join(folder,stored); open(full,'wb').write(data); rel=stored_attachment_path(full)
                x=db(); x.execute('INSERT INTO attachments(record_id,original_name,stored_name,relative_path,mime_type,size_bytes) VALUES(?,?,?,?,?,?)',(rid,original,stored,rel,mime,len(data))); x.commit(); x.close(); audit(uid,'إضافة','نسخة رقمية',rid,original)
                self.redir('/records/view?id='+str(rid)+'&success='+urllib.parse.quote('تمت إضافة النسخة الرقمية.')); return
            except ValueError as e:
                self.redir('/records/view?id='+str(form.get('record_id',''))+'&error='+urllib.parse.quote(str(e))); return
            except Exception as e:
                self.redir('/records/view?id='+str(form.get('record_id',''))+'&error='+urllib.parse.quote('تعذر إضافة الملف: '+str(e))); return
        if path in ('/settings/templates/upload','/templates/upload'):
            u=self.require()
            if not u: return
            if u['role']!='admin':
                self.redir('/settings?error='+urllib.parse.quote('المدير فقط يستطيع إضافة النماذج.')); return
            try:
                form,upload=self.multipart_file()
                self.require_csrf(form)
                tname=(form.get('name','') or '').strip()
                if not tname: raise ValueError('اسم النموذج مطلوب.')
                if not upload: raise ValueError('اختر ملف Word بصيغة doc أو docx.')
                _,orig_name,_,fdata=upload
                ext=os.path.splitext(orig_name)[1].lower()
                if ext not in TEMPLATE_EXTENSIONS: raise ValueError('صيغة الملف غير مدعومة؛ يجب أن يكون doc أو docx.')
                tfolder=resolve_templates_folder(settings().get('templates_folder_path','letter_templates'))
                os.makedirs(tfolder,exist_ok=True)
                safe_fname=safe_folder_component(os.path.splitext(orig_name)[0])+ext
                save_path=os.path.join(tfolder,safe_fname)
                c_idx=1
                while os.path.exists(save_path):
                    save_path=os.path.join(tfolder,f"{safe_folder_component(os.path.splitext(orig_name)[0])}_{c_idx}{ext}")
                    c_idx+=1
                with open(save_path,'wb') as fp: fp.write(fdata)
                rel_f=os.path.relpath(save_path,tfolder).replace(os.sep,'/')
                sp_id=int(form.get('storage_place_id') or 0) or None
                u_id=int(form.get('unit_id') or 0) or None
                y_val=int(normalize_digits(form.get('year') or 0)) or None
                x=db()
                x.execute("INSERT INTO templates(name,year,storage_place_id,unit_id,file_path) VALUES(?,?,?,?,?)",
                          (tname,y_val,sp_id,u_id,rel_f))
                x.commit(); x.close()
                audit(u['id'],'إضافة نموذج','نماذج',tname,f'unit={u_id}; year={y_val}; file={rel_f}')
                self.redir('/settings?success='+urllib.parse.quote('تم حفظ نموذج المراسلة بنجاح.')); return
            except Exception as e:
                self.redir('/settings?error='+urllib.parse.quote(str(e))); return

        form=self.postdata()
        if path=='/login':
            x=db();u=x.execute('SELECT * FROM users WHERE username=?',(form.get('username',''),)).fetchone();valid=bool(u and hashpw(form.get('password',''),u['salt'])==u['password_hash'])
            if valid:
                sid=str(uuid.uuid4()); exp=datetime.datetime.now()+datetime.timedelta(hours=12); x.execute('INSERT INTO sessions VALUES(?,?,?)',(sid,u['id'],exp));x.commit();force=bool(u['must_change_password']) if 'must_change_password' in u.keys() else False;x.close();audit(u['id'],'تسجيل دخول','نظام','');self.send_response(302);self.send_header('Location','/settings?force_password=1' if force else '/');self.send_header('Set-Cookie',f'session_id={sid}; HttpOnly; Path=/; Max-Age=43200; SameSite=Strict');self.end_headers()
            else:x.close();self.sendhtml(login_page('بيانات الدخول غير صحيحة.'),401)
            return
        u=self.require()
        if not u:return
        uid=u['id']; role=u['role']
        try: self.require_csrf(form)
        except ValueError as e: self.redir('/?error='+urllib.parse.quote(str(e))); return
        x=db()
        try:

            if path=='/print/selected-pdfs':
                direction=form.get('direction','')
                if direction not in ('incoming','outgoing'):
                    raise ValueError('اتجاه المراسلات غير صحيح.')
                raw_ids=getattr(self,'_post_values',{}).get('record_ids',[])
                ids=[]
                for value in raw_ids:
                    value=str(value).strip()
                    if value.isdigit() and int(value)>0 and int(value) not in ids:
                        ids.append(int(value))
                if not ids:
                    raise ValueError('حدد مراسلة واحدة على الأقل.')
                marks=','.join('?' for _ in ids)
                records=x.execute('SELECT r.id,r.direction,r.record_number,r.record_year,r.record_date,r.subject,u.name unit_name FROM records r LEFT JOIN units u ON u.id=r.unit_id WHERE r.direction=? AND r.id IN ('+marks+') ORDER BY r.id',(direction,*ids)).fetchall()
                if len(records)!=len(ids):
                    raise ValueError('تعذر التحقق من بعض المراسلات المحددة؛ أعد تحديد السجلات من القائمة.')
                archive_root=resolve_archive_root()
                pdf_items=[]; missing=[]
                for record in records:
                    attachments=x.execute("SELECT * FROM attachments WHERE record_id=? AND (LOWER(original_name) LIKE '%.pdf' OR LOWER(mime_type)='application/pdf') ORDER BY id",(record['id'],)).fetchall()
                    valid=[]
                    for attachment in attachments:
                        full=resolve_attachment_path(attachment['relative_path'])
                        if path_is_within(full,archive_root) and os.path.isfile(full):
                            valid.append((record,attachment))
                    if not valid:
                        missing.append(record)
                    for record_row,attachment in valid:
                        pdf_items.append((record_row,attachment))
                x.close()
                if not pdf_items:
                    msg='لا توجد ملفات PDF موجودة وآمنة داخل مسار الأرشيف للمراسلات المحددة.'
                    if missing:
                        msg+=' عدد المراسلات بلا PDF صالح: '+str(len(missing))+'.'
                    raise ValueError(msg)
                items=''.join('<section class="pdf-card"><h3>'+esc(('وارد' if r['direction']=='incoming' else 'صادر')+' '+record_number_display(r)+' — '+(r['subject'] or 'بدون موضوع'))+'</h3><p class="muted">'+esc(r['unit_name'] or '—')+' · '+esc(a['original_name'])+'</p><div class="pdf-actions"><a class="btn" target="_blank" rel="noopener" href="/attachments/open?id='+str(a['id'])+'">فتح PDF</a> <button class="btn btn-primary" type="button" onclick="printFrame(\'pdf_'+str(a['id'])+'\')">طباعة هذا الملف</button></div><iframe class="pdf-frame" id="pdf_'+str(a['id'])+'" title="'+esc(a['original_name'])+'" src="/attachments/open?id='+str(a['id'])+'"></iframe></section>' for r,a in pdf_items)
                missing_html='<div class="alert alert-error"><b>مراسلات محددة لا تحتوي على PDF موجود داخل الأرشيف:</b><ul>'+''.join('<li>'+esc(record_number_display(r)+' — '+(r['subject'] or 'بدون موضوع'))+'</li>' for r in missing)+'</ul></div>' if missing else ''
                page='<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>طباعة PDF المحدد</title><style>body{font-family:Tahoma,Arial,sans-serif;margin:18px;background:#f4f7f6;color:#202a26}.actions,.pdf-actions{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.pdf-card{background:#fff;border:1px solid #ddd;border-radius:10px;padding:14px;margin:16px 0;break-inside:avoid}.pdf-frame{width:100%;height:80vh;border:1px solid #ccc;background:#fff}@media print{.actions,.pdf-actions{display:none}.pdf-card{break-after:page}.pdf-frame{height:95vh;border:0}}</style></head><body><h1>ملفات PDF للمراسلات المحددة</h1><p>عدد ملفات PDF المتاحة: '+str(len(pdf_items))+'</p>'+missing_html+'<div class="actions"><button class="btn btn-success" onclick="printAll()">بدء الطباعة بالتتابع</button><a class="btn" href="/'+direction+'">العودة إلى المراسلات</a></div>'+items+'<script>function printFrame(id){var f=document.getElementById(id);if(!f)return;try{f.contentWindow.focus();f.contentWindow.print();}catch(e){window.open(f.src,\'_blank\');}}function printAll(){var a=Array.prototype.slice.call(document.querySelectorAll(\'.pdf-frame\'));var i=0;function next(){if(i>=a.length)return;var f=a[i++];try{f.contentWindow.focus();f.contentWindow.print();}catch(e){window.open(f.src,\'_blank\');}if(i<a.length)setTimeout(next,1800);}next();}</script></body></html>'
                self.sendhtml(page)
                return
            if path in ('/incoming/new','/outgoing/new','/records/edit'):
                rid=urllib.parse.parse_qs(p.query).get('id',[''])[0]
                if path=='/records/edit':
                    existing=x.execute('SELECT direction, kind, record_year, physical_location, unit_id FROM records WHERE id=?',(rid,)).fetchone()
                    if not existing: raise ValueError('السجل غير موجود.')
                    direction=existing['direction']
                else:
                    direction='incoming' if path.startswith('/incoming') else 'outgoing'
                num_raw=form.get('record_number','')
                num=normalize_digits(num_raw).strip()
                if not num or not num.isdigit(): raise ValueError('رقم المراسلة يجب أن يكون رقماً صحيحاً فقط.')
                ref=form.get('reference','');sub=form.get('subject','');rd=form.get('record_date','')
                unit_id=form.get('unit_id','');unit_name=form.get('unit_name','').strip();notes=form.get('notes','')
                kind = existing['kind'] if path=='/records/edit' and existing['kind'] in ('mail','telegram') else 'mail'

                if not unit_id and unit_name:
                    candidates=x.execute("SELECT u.id,u.name FROM units u WHERE u.name=? ORDER BY u.id",(unit_name,)).fetchall()
                    if len(candidates)==1: unit_id=str(candidates[0]['id'])
                    elif len(candidates)>1: raise ValueError('اسم الوحدة غير كافٍ لتحديد الوحدة. اختر الوحدة من القائمة.')
                if not all([num,rd,unit_id]): raise ValueError('يرجى إكمال الحقول الإلزامية: رقم المراسلة والتاريخ والوحدة.')
                try:
                    dt = datetime.datetime.strptime(rd,'%Y-%m-%d')
                    record_year = dt.year
                except ValueError:
                    raise ValueError('صيغة التاريخ غير صحيحة. استخدم التاريخ بالشكل YYYY-MM-DD.')
                ur=x.execute('SELECT storage_place_id FROM units WHERE id=?',(unit_id,)).fetchone()
                if not ur:raise ValueError('الوحدة المحددة غير موجودة.')

                # Outgoing Uniqueness Check
                if direction=='outgoing':
                    dup_params=['outgoing',num,record_year]
                    dup_sql="SELECT id FROM records WHERE direction=? AND record_number=? AND record_year=?"
                    if path=='/records/edit':
                        dup_sql += ' AND id<>?'
                        dup_params.append(rid)
                    if x.execute(dup_sql,tuple(dup_params)).fetchone():
                        raise ValueError(f'رقم المراسلة الصادرة {num}/{record_year} موجود مسبقاً في النظام. لا يمكن تكرار نفس الرقم في نفس السنة.')

                if path=='/records/edit':
                    if not rid:raise ValueError('معرّف السجل مفقود.')
                    # For historical physical_location: preserve it unless the user explicitly changed the unit.
                    if str(existing['unit_id']) == str(unit_id):
                        loc = existing['physical_location']
                    else:
                        loc=effective_location(x,unit_id)
                        if not loc: raise ValueError('لم يتم تحديد مكان حفظ لهذه الوحدة. أدخل مكان الحفظ من إعدادات الوحدة أولاً.')
                    x.execute('UPDATE records SET direction=?,kind=?,record_number=?,record_year=?,reference=?,subject=?,record_date=?,unit_id=?,physical_location=?,notes=? WHERE id=?',(direction,kind,num,record_year,ref,sub,rd,unit_id,loc,notes,rid));action='تعديل';ident=rid
                else:
                    loc=effective_location(x,unit_id)
                    if not loc: raise ValueError('لم يتم تحديد مكان حفظ لهذه الوحدة. أدخل مكان الحفظ من إعدادات الوحدة أولاً.')
                    cur=x.execute('INSERT INTO records(direction,kind,record_number,record_year,reference,subject,record_date,unit_id,physical_location,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(direction,kind,num,record_year,ref,sub,rd,unit_id,loc,notes,datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')));ident=cur.lastrowid;action='إضافة'
                x.commit();x.close()
                folder_warning=''
                try:
                    ensure_record_archive_folders(ident)
                except Exception as folder_error:
                    folder_warning='&error='+urllib.parse.quote('تم حفظ السجل، لكن تعذر إنشاء مجلداته: '+str(folder_error))
                audit(uid,action,'وارد' if direction=='incoming' else 'صادر',ident,num)
                self.redir('/records/view?id='+str(ident)+folder_warning);return
            if path=='/records/link-unignore' or path=='/records/link-ignore':
                self.redir('/dashboard?error='+urllib.parse.quote('هذه الخاصية لم تعد مدعومة في النسخة الجديدة.'))
                return
            if path=='/records/link':
                incoming_id=form.get('incoming_id',''); outgoing_id=form.get('outgoing_id','')
                ir=x.execute("SELECT id,direction FROM records WHERE id=?",(incoming_id,)).fetchone()
                orow=x.execute("SELECT id,direction FROM records WHERE id=?",(outgoing_id,)).fetchone()
                if not ir or not orow or ir['direction']!='incoming' or orow['direction']!='outgoing':
                    raise ValueError('السجلان المحددان ليسا واردًا وصادرًا صالحين.')
                x.execute('INSERT OR IGNORE INTO record_links(incoming_id,outgoing_id) VALUES(?,?)',(incoming_id,outgoing_id))
                x.commit(); x.close(); audit(uid,'ربط','وارد/صادر',incoming_id,'الصادر '+outgoing_id)
                self.redir('/records/view?id='+str(incoming_id)+'&success='+urllib.parse.quote('تم الربط بنجاح، وتم إخفاء احتمال الارتباط بهذا الزوج.')+'&refresh=1'); return
            if path=='/records/unlink':
                lid=form.get('link_id',''); link=x.execute('SELECT incoming_id,outgoing_id FROM record_links WHERE id=?',(lid,)).fetchone()
                if not link: raise ValueError('الرابط غير موجود.')
                return_id=form.get('return_id','')
                if str(link['incoming_id'])!=str(form.get('incoming_id','')) and str(link['outgoing_id'])!=str(form.get('outgoing_id','')): raise ValueError('الرابط غير صالح.')
                x.execute('DELETE FROM record_links WHERE id=?',(lid,)); x.commit(); x.close(); audit(uid,'فك ربط','وارد/صادر',lid,''); self.redir('/records/view?id='+return_id+'&success='+urllib.parse.quote('تم فك الربط دون حذف أي سجل.')); return
            if path=='/attachments/delete':
                aid=form.get('id',''); a=x.execute('SELECT * FROM attachments WHERE id=?',(aid,)).fetchone()
                if not a: raise ValueError('النسخة الرقمية غير موجودة.')
                managed_folder=record_archive_folder(a['record_id'])
                x.execute('DELETE FROM attachments WHERE id=?',(aid,)); x.commit(); x.close()
                try:
                    remove_managed_attachment_file(a['relative_path'],managed_folder)
                except Exception: pass
                audit(uid,'حذف','نسخة رقمية',a['record_id'],a['original_name']); self.redir('/records/view?id='+str(a['record_id'])+'&success='+urllib.parse.quote('تم حذف سجل المرفق؛ لا تُحذف الملفات خارج مجلد السجل الذي يديره البرنامج.')); return
            if path=='/records/delete':
                rid=form.get('id','')
                r=x.execute('SELECT direction,record_number FROM records WHERE id=?',(rid,)).fetchone()
                if not r: raise ValueError('السجل غير موجود.')
                attachments=x.execute('SELECT relative_path FROM attachments WHERE record_id=?',(rid,)).fetchall()
                managed_folder=record_archive_folder(rid)
                archive_root=os.path.realpath(resolve_archive_root())
                x.execute('DELETE FROM records WHERE id=?',(rid,))
                x.commit();x.close()
                removed=0
                for item in attachments:
                    try:
                        full=resolve_attachment_path(item['relative_path'])
                        if remove_managed_attachment_file(item['relative_path'],managed_folder,archive_root):
                            removed+=1
                        parent=os.path.dirname(os.path.realpath(full))
                        while path_is_within(parent,archive_root):
                            if os.path.isdir(parent) and not os.listdir(parent):
                                os.rmdir(parent); parent=os.path.dirname(parent)
                            else:
                                break
                    except Exception:
                        pass
                audit(uid,'حذف','مراسلة',rid,r['record_number'])
                self.redir('/search?success='+urllib.parse.quote('تم حذف السجل والنسخ الرقمية المرتبطة به. الملفات التي تعذر حذفها لم تُحذف قسرًا.'));return
            if path=='/quick-links/add' or path=='/quick-links/edit' or path=='/quick-links/delete':
                if role!='admin':raise ValueError('هذه العملية متاحة للمدير فقط.')
                if path=='/quick-links/delete':x.execute('DELETE FROM quick_links WHERE id=?',(form.get('id',''),));msg='تم حذف الزر.'
                else:
                    title=form.get('title',''); link=form.get('path',''); icon=form.get('icon','📄');color=form.get('color','#16a085');order=int(form.get('sort_order','0') or 0)
                    if not title or not link:raise ValueError('اسم الزر ومسار الملف مطلوبان.')
                    if path.endswith('/add'):x.execute('INSERT INTO quick_links(title,path,icon,color,sort_order) VALUES(?,?,?,?,?)',(title,link,icon,color,order));msg='تمت إضافة الزر.'
                    else:x.execute('UPDATE quick_links SET title=?,path=?,icon=?,color=?,sort_order=? WHERE id=?',(title,link,icon,color,order,urllib.parse.parse_qs(p.query).get('id',[''])[0]));msg='تم تعديل الزر.'
                x.commit();x.close();audit(uid,'تعديل','فتح سريع','',msg);self.redir('/quick-links?success='+urllib.parse.quote(msg));return
            if path=='/structure/add':
                if role!='admin':raise ValueError('هذه العملية متاحة للمدير فقط.')
                typ=form.get('entity_type');name=form.get('name','').strip();sp_id=form.get('storage_place_id','') or None
                if not name:raise ValueError('الاسم مطلوب.')
                if typ=='storage':
                    x.execute('INSERT INTO storage_places(name) VALUES(?)',(name,)); item='مكان تخزين'
                elif typ=='unit':
                    if not sp_id: raise ValueError('يجب ربط الوحدة بمكان تخزين.')
                    x.execute('INSERT INTO units(storage_place_id,name) VALUES(?,?)',(sp_id,name));item='وحدة'
                else:raise ValueError('نوع العنصر غير صحيح.')
                x.commit();x.close();audit(uid,'إضافة',item,name);self.redir('/settings?success='+urllib.parse.quote('تمت الإضافة بنجاح.'));return
            if path=='/structure/edit-storage':
                if role!='admin': raise ValueError('المدير فقط.')
                sid=form.get('id',''); name=form.get('name','').strip()
                if not sid or not name: raise ValueError('الاسم مطلوب.')
                x.execute('UPDATE storage_places SET name=? WHERE id=?',(name,sid)); x.commit(); x.close(); audit(uid,'تعديل','مكان تخزين',sid,name); self.redir('/settings?success='+urllib.parse.quote('تم تعديل مكان التخزين.')); return
            if path=='/structure/edit-unit':
                if role!='admin': raise ValueError('المدير فقط.')
                uid2=form.get('id',''); name=form.get('name','').strip(); sp_id=form.get('storage_place_id','') or None
                if not uid2 or not name: raise ValueError('اسم الوحدة مطلوب.')
                if not sp_id: raise ValueError('يجب اختيار مكان تخزين الوحدة.')
                x.execute('UPDATE units SET storage_place_id=?,name=? WHERE id=?',(sp_id,name,uid2))
                x.commit(); x.close(); audit(uid,'تعديل','وحدة',uid2,name); self.redir('/settings?success='+urllib.parse.quote('تم تعديل الوحدة وحفظ التغيير (لا تتأثر السجلات القديمة).')); return
            if path=='/storage/delete':
                if role!='admin':raise ValueError('المدير فقط.')
                sid=form.get('id','');count=x.execute('SELECT COUNT(*) FROM units WHERE storage_place_id=?',(sid,)).fetchone()[0]
                if count:raise ValueError('لا يمكن حذف مكان التخزين لأنه مرتبط بوحدات. يجب حذف الوحدات أولاً أو نقلها.')
                x.execute('DELETE FROM storage_places WHERE id=?',(sid,));x.commit();x.close();audit(uid,'حذف','مكان تخزين',sid,'');self.redir('/settings?success='+urllib.parse.quote('تم الحذف.'));return
            if path=='/units/delete':
                if role!='admin':raise ValueError('المدير فقط.')
                uid2=form.get('id','');count=x.execute('SELECT COUNT(*) FROM records WHERE unit_id=?',(uid2,)).fetchone()[0]
                if count:raise ValueError('لا يمكن حذف الوحدة لأنها مرتبطة بسجلات.')
                x.execute('DELETE FROM units WHERE id=?',(uid2,));x.commit();x.close();self.redir('/settings?success='+urllib.parse.quote('تم الحذف.'));return
            if path=='/templates/open':
                filename=str(form.get('filename','')).strip().replace(chr(92),'/')
                parts=filename.split('/')
                if not filename or filename.startswith('/') or any(part in ('','..','.') for part in parts) or ':' in parts[0]:
                    raise ValueError('مسار ملف النموذج غير صالح.')
                ext=os.path.splitext(parts[-1])[1].lower()
                if ext not in TEMPLATE_EXTENSIONS:
                    raise ValueError('يمكن فتح ملفات Word من مجلد النماذج فقط.')
                folder=resolve_templates_folder()
                if not os.path.isdir(folder):
                    raise ValueError('مجلد نماذج المراسلات غير موجود. تحقق من المسار في صفحة النماذج.')
                filepath=os.path.abspath(os.path.join(folder,*parts))
                root_real=os.path.realpath(folder)
                full_real=os.path.realpath(filepath)
                try:
                    inside=os.path.commonpath([full_real,root_real])==root_real
                except ValueError:
                    inside=False
                if not inside or not os.path.isfile(full_real):
                    raise ValueError('ملف النموذج غير موجود داخل المجلد المحدد.')
                try:
                    x.close()
                    if os.name=='nt': os.startfile(full_real)
                    else: webbrowser.open('file://'+urllib.parse.quote(os.path.abspath(full_real)))
                    audit(uid,'فتح نموذج','نموذج',filename,filename)
                    self.redir('/templates?success='+urllib.parse.quote('تم فتح النموذج مباشرة. يمكنك تحريره وحفظه في Word.'))
                except Exception as e:
                    self.redir('/templates?error='+urllib.parse.quote('تعذر فتح النموذج: '+str(e)))
                return
            if path=='/templates/path':
                if role!='admin':raise ValueError('تغيير مسار النماذج متاح للمدير فقط.')
                raw=form.get('folder_path','').strip()
                if not raw: raise ValueError('أدخل مسار مجلد النماذج.')
                folder=resolve_templates_folder(raw)
                action=form.get('action','save')
                if action=='test':
                    if not os.path.isdir(folder): raise ValueError('المجلد غير موجود أو ليس مجلدًا صالحًا.')
                    os.listdir(folder)
                    x.close()
                    self.redir('/templates?picked_path='+urllib.parse.quote(raw)+'&tested=1&success='+urllib.parse.quote('نجح اختبار المسار ويمكن قراءة المجلد.'))
                    return
                if not os.path.isdir(folder): raise ValueError('لا يمكن حفظ المسار لأن المجلد غير موجود. استخدم الاستعراض أو أنشئ المجلد أولاً.')
                x.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('templates_folder_path',?)",(folder,))
                x.commit();x.close()
                self.redir('/templates?success='+urllib.parse.quote('تم حفظ مسار مجلد النماذج.'))
                return
            if path=='/security/shutdown':
                self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
                self.wfile.write(b'{"status":"shutting_down"}')
                def exit_job():
                    time.sleep(0.2)
                    _request_safe_shutdown('طلب المستخدم إغلاق البرنامج من نافذة الحماية.')
                threading.Thread(target=exit_job,daemon=True).start(); return

            if path in ('/settings/templates/delete','/templates/delete'):
                u=self.require()
                if not u: return
                if u['role']!='admin': raise ValueError('المدير فقط يستطيع حذف مرجع النموذج.')
                form=self.parse_body()
                tid=int(form.get('id') or 0)
                x=db()
                row=x.execute('SELECT name,file_path FROM templates WHERE id=?',(tid,)).fetchone()
                if row:
                    x.execute('DELETE FROM templates WHERE id=?',(tid,))
                    x.commit()
                    audit(u['id'],'حذف مرجع نموذج','نماذج',tid,row['name'])
                x.close()
                self.redir('/settings?success='+urllib.parse.quote('تم حذف مرجع النموذج من البرنامج دون حذف الملف الأصلي من القرص.')); return
            if path in ('/templates/open','/settings/templates/open'):
                u=self.require()
                if not u: return
                form=self.parse_body()
                tid=form.get('id','')
                fname=form.get('filename','')
                tfolder=resolve_templates_folder(settings().get('templates_folder_path','letter_templates'))
                target_f=None
                if tid:
                    x=db()
                    row=x.execute('SELECT file_path FROM templates WHERE id=?',(tid,)).fetchone()
                    x.close()
                    if row: target_f=os.path.realpath(os.path.join(tfolder,row['file_path']))
                elif fname:
                    target_f=os.path.realpath(os.path.join(tfolder,fname))
                if not target_f or not os.path.isfile(target_f) or not path_is_within(target_f,tfolder):
                    raise ValueError('تعذر العثور على ملف النموذج.')
                opened=False
                if os.name=='nt':
                    try:
                        os.startfile(target_f)
                        opened=True
                    except Exception: pass
                if not opened:
                    self.redir(f'/templates/download?id={tid}')
                    return
                self.redir('/settings?success='+urllib.parse.quote('تم إرسال أمر فتح النموذج في Word.')); return
            if path=='/settings/security':
                if role!='admin':raise ValueError('المدير فقط.')
                x.execute('UPDATE settings SET value=? WHERE key=?',('1' if form.get('security_usb_warning') else '0','security_usb_warning'))
                x.execute('UPDATE settings SET value=? WHERE key=?',('1' if form.get('security_internet_shutdown') else '0','security_internet_shutdown'))
                x.execute('UPDATE settings SET value=? WHERE key=?',('1' if form.get('security_local_network_warning') else '0','security_local_network_warning'))
                x.commit();x.close();self.redir('/security?success='+urllib.parse.quote('تم حفظ إعدادات الحماية بنجاح.'));return
            if path=='/storage/smart-sync':
                if role!='admin': raise ValueError('التحديث الذكي متاح للمدير فقط.')
                x.close()
                report=_archive_import_scan(resolve_archive_root(),True,None)
                audit(uid,'تحديث ذكي','الأرشيف','',f"years={report.get('years_count',0)}; records={report['created_records']}; linked={report['linked_files']}; already={report['already_linked']}; conflicts={report['conflicts']}")
                msg=f"تم إكمال التحديث الذكي بنجاح: استيعاب جميع السنوات والمخازن والوحدات المكتملة، إنشاء {report['created_records']} سجل جديد، ربط {report['linked_files']} ملف PDF، مع تجاوز {report['already_linked']} ملف مسجل مسبقًا لمنع التكرار."
                self.redir('/settings?success='+urllib.parse.quote(msg)); return
            if path=='/pdf-linker/match':
                if role!='admin': raise ValueError('ربط ملفات PDF متاح للمدير فقط.')
                raw=str(form.get('folder_path','')).strip()
                folder=os.path.abspath(os.path.expanduser(os.path.expandvars(raw)))
                if not os.path.isdir(folder): raise ValueError('مجلد PDF غير موجود أو غير قابل للوصول.')
                if os.name=='nt' and folder.startswith(os.sep*2): raise ValueError('اختر مجلدًا محليًا وليس مسار شبكة.')
                folder_real=os.path.realpath(folder)
                x.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('pdf_linker_folder_path',?)",(folder_real,))
                records=x.execute("SELECT r.id,r.direction,r.record_number,r.record_year,r.record_date,r.subject,r.unit_id,u.name AS unit_name,s.name AS storage_name FROM records r LEFT JOIN units u ON u.id=r.unit_id LEFT JOIN storage_places s ON s.id=u.storage_place_id ORDER BY r.id").fetchall()
                def _norm_pdf_name(value):
                    value=normalize_digits(str(value or '')).casefold()
                    return re.sub(r'[\s_]+','',value)
                candidates=[]
                existing_links=x.execute('SELECT id,record_id,relative_path FROM attachments').fetchall()
                skipped_linked=0
                file_count=0
                for walk_root,dirs,files in os.walk(folder_real):
                    dirs[:]=[d for d in dirs if not d.startswith('.')]
                    for filename in files:
                        if not filename.lower().endswith('.pdf'): continue
                        file_count+=1
                        if file_count>5000: raise ValueError('تجاوز الفحص 5000 ملف PDF؛ قسّم المجلدات ثم أعد الفحص.')
                        file_entry=os.path.join(walk_root,filename)
                        if os.path.islink(file_entry): continue
                        full=os.path.realpath(file_entry)
                        if not path_is_within(full,folder_real): continue
                        stem=_norm_pdf_name(os.path.splitext(filename)[0])
                        exact=[r for r in records if _norm_pdf_name(r['record_number'])==stem]
                        found=exact
                        if not found:
                            prefix=[r for r in records if stem.startswith(_norm_pdf_name(r['record_number'])+'.') and _norm_pdf_name(r['record_number'])]
                            if prefix:
                                longest=max(len(_norm_pdf_name(r['record_number'])) for r in prefix)
                                found=[r for r in prefix if len(_norm_pdf_name(r['record_number']))==longest]
                        linked=[a for a in existing_links if os.path.normcase(os.path.realpath(resolve_attachment_path(a['relative_path'])))==os.path.normcase(full)]
                        if linked:
                            skipped_linked+=1
                            continue
                        if len(found)==1:
                            r=found[0]
                            candidates.append({'path':full,'filename':filename,'record_id':int(r['id']),'direction':r['direction'],'number':str(r['record_number']),'year':str(r['record_year']),'unit':str(r['unit_name'] or ''),'storage':str(r['storage_name'] or '')})
                        elif len(found)>1:
                            candidates.append({'path':full,'filename':filename,'record_id':0,'direction':'ambiguous','number':'ملتبس','year':'','unit':'، '.join(sorted(set(str(r['unit_name'] or '') for r in found))),'storage':''})
                x.commit()
                x.close()
                payload=json.dumps([z for z in candidates if z['record_id']>0],ensure_ascii=False,separators=(',',':'))
                table_rows=[]
                for z in candidates:
                    if z['record_id']>0:
                        d='وارد' if z['direction']=='incoming' else 'صادر'
                        table_rows.append('<tr><td><input type="checkbox" checked disabled></td><td>'+esc(z['filename'])+'</td><td>'+d+'</td><td>'+esc(z['number'])+'/'+esc(z['year'])+'</td><td>'+esc(z['unit'])+'</td></tr>')
                    else:
                        table_rows.append('<tr><td>—</td><td>'+esc(z['filename'])+'</td><td colspan="3">ملتبس: '+esc(z['unit'])+' — لم يُربط</td></tr>')
                content=f'''<div class="card"><h2>معاينة ربط PDF</h2><p>عدد الملفات المفحوصة: {file_count} — مرشحات للربط: {sum(1 for z in candidates if z['record_id']>0)} — حالات ملتبسة: {sum(1 for z in candidates if not z['record_id'])} — ملفات سبق تسجيلها وتجاوزها: {skipped_linked}</p><p>لم يُقرأ محتوى أي PDF. تحقق من كل اسم ورقم ووحدة قبل التأكيد.</p><table><thead><tr><th>اختيار</th><th>اسم الملف</th><th>الاتجاه</th><th>السجل</th><th>الوحدة/الحالة</th></tr></thead><tbody>{''.join(table_rows) or '<tr><td colspan="5">لا توجد ملفات قابلة للربط.</td></tr>'}</tbody></table><form method="POST" action="/pdf-linker/confirm"><input type="hidden" name="folder_path" value="{esc(folder_real)}"><input type="hidden" name="matches_json" value="{esc(payload)}"><button class="btn btn-success" {'disabled' if not any(z['record_id']>0 for z in candidates) else ''} onclick="return confirm('تأكيد إنشاء روابط قاعدة البيانات للنتائج الواضحة فقط؟')">تأكيد الروابط الواضحة</button> <a class="btn" href="/pdf-linker">إلغاء</a></form></div>'''
                self.sendhtml(base('معاينة ربط PDF',content,'pdf-linker',uid=uid))
                return
            if path=='/pdf-linker/confirm':
                if role!='admin': raise ValueError('ربط ملفات PDF متاح للمدير فقط.')
                folder=os.path.realpath(str(form.get('folder_path','')).strip())
                configured=os.path.realpath(str(settings().get('pdf_linker_folder_path','')).strip() or folder)
                if not os.path.isdir(folder) or folder!=configured: raise ValueError('مسار المجلد تغيّر منذ المعاينة؛ أعد الفحص.')
                try: items=json.loads(form.get('matches_json','[]'))
                except Exception: raise ValueError('بيانات المعاينة غير صالحة؛ أعد الفحص.')
                if not isinstance(items,list) or len(items)>5000: raise ValueError('قائمة الروابط غير صالحة.')
                all_number_rows=x.execute("SELECT id,record_number FROM records").fetchall()
                def _norm_confirm(value):
                    value=normalize_digits(str(value or '')).casefold()
                    return re.sub(r'[\s_]+','',value)
                created=0; conflicts=0
                for item in items:
                    if not isinstance(item,dict): continue
                    path_value=os.path.realpath(str(item.get('path','')))
                    rid=item.get('record_id')
                    if not isinstance(rid,int) or rid<=0 or not path_is_within(path_value,folder) or not os.path.isfile(path_value) or not path_value.lower().endswith('.pdf'): conflicts+=1; continue
                    filename=os.path.basename(path_value)
                    # Revalidate filename-to-record matching at confirmation; never trust a submitted record id alone.
                    row=x.execute("SELECT id,record_number FROM records WHERE id=?",(rid,)).fetchone()
                    if not row: conflicts+=1; continue
                    stem=_norm_confirm(os.path.splitext(filename)[0])
                    exact=[z for z in all_number_rows if _norm_confirm(z['record_number'])==stem]
                    if exact:
                        matched=exact
                    else:
                        pref=[z for z in all_number_rows if _norm_confirm(z['record_number']) and stem.startswith(_norm_confirm(z['record_number'])+'.')]
                        if pref:
                            longest=max(len(_norm_confirm(z['record_number'])) for z in pref)
                            matched=[z for z in pref if len(_norm_confirm(z['record_number']))==longest]
                        else:
                            matched=[]
                    if len(matched)!=1 or int(matched[0]['id'])!=rid:
                        conflicts+=1; continue
                    exists=[a for a in x.execute('SELECT id,relative_path FROM attachments').fetchall() if os.path.normcase(os.path.realpath(resolve_attachment_path(a['relative_path'])))==os.path.normcase(path_value)]
                    if exists: conflicts+=1; continue
                    size=os.path.getsize(path_value)
                    x.execute("INSERT INTO attachments(record_id,original_name,stored_name,relative_path,mime_type,size_bytes) VALUES(?,?,?,?,?,?)",(rid,filename,filename,path_value,'application/pdf',size))
                    created+=1
                x.commit()
                x.close()
                audit(uid,'ربط ملفات PDF','مرفقات','',str(created)+' روابط؛ '+str(conflicts)+' تعارضات/تغييرات')
                self.redir('/pdf-linker?success='+urllib.parse.quote('تم إنشاء '+str(created)+' رابطًا. تم تجاوز '+str(conflicts)+' حالة غير آمنة أو متعارضة. لم تُنقل الملفات ولم يُحذف أي ملف.'))
                return
            if path=='/attachments/unlink_safe':
                if role!='admin': raise ValueError('فك ربط PDF متاح للمدير فقط.')
                aid=form.get('attachment_id','')
                row=x.execute("SELECT id,record_id,original_name FROM attachments WHERE id=?",(aid,)).fetchone()
                if not row: raise ValueError('الرابط غير موجود.')
                x.execute('DELETE FROM attachments WHERE id=?',(row['id'],))
                x.commit()
                x.close()
                audit(uid,'فك ربط PDF','مرفقات',row['id'],row['original_name'])
                self.redir('/pdf-linker?success='+urllib.parse.quote('تم حذف علاقة الربط من قاعدة البيانات فقط؛ بقي ملف PDF وسجل الأرشيف دون تغيير.'))
                return

            if path=='/storage/import-confirm':
                if role!='admin': raise ValueError('الاستيراد الذكي متاح للمدير فقط.')
                if form.get('confirm_import')!='1': raise ValueError('يجب تأكيد الاستيراد صراحةً.')
                years=[normalize_digits(v).strip() for v in str(form.get('years','')).split(',') if re.fullmatch(r'\d{4}',normalize_digits(v).strip())]
                if not years: raise ValueError('اختر سنة واحدة على الأقل من معاينة الاستيراد.')
                x.close()
                report=_archive_import_scan(resolve_archive_root(),True,years)
                audit(uid,'استيراد ذكي','الأرشيف','',f"years={','.join(years)}; records={report['created_records']}; linked={report['linked_files']}; conflicts={report['conflicts']}; missing={report['missing']}")
                msg=f"اكتمل الاستيراد: {report['created_records']} سجلًا جديدًا و{report['linked_files']} ملفًا مرتبطًا؛ {report['already_linked']} روابط موجودة تم تجاوزها؛ {report['conflicts']} تعارضات و{report['missing']} ملفات بلا سجل واضح."
                self.redir('/storage/structure?success='+urllib.parse.quote(msg)); return
            if path=='/storage/structure/create':
                if role!='admin':raise ValueError('معاينة هيكل التخزين متاحة للمدير فقط.')
                if form.get('confirm_create')!='1':raise ValueError('لم يتم تأكيد إنشاء المجلدات.')
                root=resolve_archive_root()
                c2=db()
                rows=c2.execute('''SELECT DISTINCT r.record_year,u.name AS unit_name,s.name AS storage_name
                                   FROM records r LEFT JOIN units u ON u.id=r.unit_id
                                   LEFT JOIN storage_places s ON s.id=u.storage_place_id
                                   ORDER BY r.record_year DESC,s.name,u.name''').fetchall()
                c2.close()
                if rows and not os.path.exists(root):
                    os.makedirs(root,exist_ok=True)
                created=0; conflicts=[]
                for row in rows:
                    base_path=os.path.join(root,safe_folder_component(row['record_year']),safe_folder_component(row['storage_name']),safe_folder_component(row['unit_name']))
                    for direction_name in ('وارد','صادر'):
                        target=os.path.join(base_path,direction_name)
                        state,conflict,missing=archive_directory_status(root,target)
                        if state=='تعارض':
                            conflicts.append(conflict or target)
                            continue
                        if state=='مفقود':
                            created+=len(missing)
                            os.makedirs(target,exist_ok=True)
                audit(uid,'إنشاء مجلدات ناقصة','هيكل الأرشيف','',f'created={created}; conflicts={len(conflicts)}')
                msg=f'تم إنشاء {created} مجلدًا ناقصًا. لم تُمس العناصر غير المعروفة.'
                if conflicts: msg+=' تعارضات لم تُغيّر: '+'؛ '.join(conflicts[:5])
                self.redir('/storage/structure?success='+urllib.parse.quote(msg));return
            if path=='/storage/migrate-attachments':
                if role!='admin':raise ValueError('تنظيم المرفقات متاح للمدير فقط.')
                if form.get('confirm_migrate')!='1':raise ValueError('لم يتم تأكيد نقل المرفقات.')
                new_root=resolve_archive_root(form.get('new_root','').strip())
                old_root=resolve_archive_root()
                parent=os.path.dirname(new_root)
                if not os.path.isdir(parent): raise ValueError('المجلد الأب للمسار المقترح غير موجود.')
                if os.path.exists(new_root) and not os.path.isdir(new_root): raise ValueError('المسار المقترح موجود لكنه ليس مجلدًا.')
                plan=attachment_migration_plan(new_root)
                blocked=[item for item in plan if item['status'] not in ('في مكانه الصحيح','سيُنقل بعد التأكيد')]
                if blocked:
                    msg='توجد حالات تمنع النقل ولم يُغيّر البرنامج أي ملف: '+'؛ '.join(str(item.get('original_name') or item['id'])+' — '+item['status'] for item in blocked[:5])
                    x.close()
                    self.redir('/storage/migrate-preview?new_root='+urllib.parse.quote(new_root,safe='')+'&error='+urllib.parse.quote(msg));return
                created_root=False; copied_files=[]; temp_files=[]
                try:
                    if not os.path.exists(new_root):
                        os.makedirs(new_root,exist_ok=True); created_root=True
                    for item in plan:
                        if item['status']!='سيُنقل بعد التأكيد':
                            continue
                        destination=item['destination']
                        os.makedirs(os.path.dirname(destination),exist_ok=True)
                        if os.path.exists(destination):
                            raise ValueError('ظهر تعارض أثناء التنفيذ عند الوجهة: '+destination)
                        temp_destination=destination+'.migrating_'+uuid.uuid4().hex
                        temp_files.append(temp_destination)
                        shutil.copy2(item['source'],temp_destination)
                        if sha256_file(item['source'])!=sha256_file(temp_destination):
                            raise ValueError('لم تتطابق بصمة الملف بعد النسخ: '+str(item.get('original_name') or item['id']))
                        if os.path.exists(destination):
                            raise ValueError('ظهرت وجهة أخرى أثناء النقل: '+destination)
                        os.replace(temp_destination,destination)
                        temp_files.remove(temp_destination);copied_files.append(destination)
                    x.execute('BEGIN IMMEDIATE')
                    for item in plan:
                        destination=item['destination'] if item['status']=='سيُنقل بعد التأكيد' else item['source']
                        x.execute('UPDATE attachments SET relative_path=?,stored_name=? WHERE id=?',
                                  (stored_attachment_path(destination),os.path.basename(destination),item['id']))
                    try:
                        root_setting=os.path.relpath(os.path.realpath(new_root),os.path.realpath(BASE_DIR))
                    except ValueError:
                        root_setting=new_root
                    if root_setting=='..' or root_setting.startswith('..'+os.sep): root_setting=new_root
                    x.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('archive_root_path',?)",(root_setting,))
                    x.commit();x.close()
                except Exception:
                    try:x.rollback();x.close()
                    except Exception:pass
                    for path_to_remove in copied_files+temp_files:
                        try:
                            if os.path.isfile(path_to_remove):os.remove(path_to_remove)
                        except Exception:pass
                    if created_root:
                        try:
                            if os.path.isdir(new_root) and not os.listdir(new_root):os.rmdir(new_root)
                        except Exception:pass
                    raise
                delete_failures=[]; removed_sources=0
                for item in plan:
                    source=item['source']
                    destination=item['destination'] if item['status']=='سيُنقل بعد التأكيد' else item['source']
                    if os.path.normcase(os.path.realpath(source))==os.path.normcase(os.path.realpath(destination)):
                        continue
                    try:
                        c2=db()
                        still_used=c2.execute('SELECT COUNT(*) FROM attachments WHERE relative_path=?',(item['relative_path'],)).fetchone()[0]
                        c2.close()
                        if still_used==0 and os.path.isfile(source):
                            os.remove(source);removed_sources+=1
                            try:
                                source_real=os.path.realpath(source);old_real=os.path.realpath(old_root)
                                if os.path.commonpath([source_real,old_real])==old_real:
                                    parent_dir=os.path.dirname(source)
                                    while os.path.realpath(parent_dir)!=old_real and os.path.commonpath([os.path.realpath(parent_dir),old_real])==old_real:
                                        if os.path.isdir(parent_dir) and not os.listdir(parent_dir):
                                            os.rmdir(parent_dir);parent_dir=os.path.dirname(parent_dir)
                                        else:break
                            except Exception:pass
                    except Exception as delete_error:
                        delete_failures.append(str(source)+': '+str(delete_error))
                c3=db()
                record_ids=[row[0] for row in c3.execute('SELECT id FROM records').fetchall()]
                c3.close()
                folder_errors=[]
                for record_id in record_ids:
                    try:ensure_record_archive_folders(record_id,new_root)
                    except Exception as folder_error:folder_errors.append(str(folder_error))
                audit(uid,'تنظيم المرفقات','الأرشيف',new_root,f'attachments={len(plan)}; moved={len(copied_files)}; removed_sources={removed_sources}')
                msg=f'تم تنظيم {len(plan)} مرفقًا؛ نُسخت وتحققت {len(copied_files)} ملفات، وأزيلت {removed_sources} نسخ مصدرية بعد تحديث قاعدة البيانات.'
                if delete_failures:msg+=' بقيت بعض النسخ المصدرية ولم تُحذف: '+'؛ '.join(delete_failures[:3])
                if folder_errors:msg+=' تعذر إنشاء بعض المجلدات الفارغة: '+str(len(folder_errors))
                self.redir('/settings?success='+urllib.parse.quote(msg));return
            if path=='/storage/root':
                if role!='admin':raise ValueError('تغيير مسار الأرشيف متاح للمدير فقط.')
                raw=form.get('root_path','').strip()
                if not raw: raise ValueError('أدخل مسار مكان التخزين الرئيسي.')
                root=resolve_archive_root(raw)
                action=form.get('action','save')
                if action=='test':
                    parent=root if os.path.isdir(root) else os.path.dirname(root)
                    if not os.path.isdir(parent): raise ValueError('المجلد المحدد والمجلد الأب غير موجودين.')
                    if os.path.exists(root) and not os.path.isdir(root): raise ValueError('المسار موجود لكنه ليس مجلدًا.')
                    probe_dir=root if os.path.isdir(root) else parent
                    probe=os.path.join(probe_dir,'.digital_archive_path_test_'+uuid.uuid4().hex)
                    try:
                        with open(probe,'xb') as probe_file:
                            probe_file.write(b'Digital Archive Pro archive path test')
                        os.remove(probe)
                    except Exception as probe_error:
                        try:
                            if os.path.isfile(probe): os.remove(probe)
                        except Exception: pass
                        raise ValueError('تعذر إنشاء ملف اختبار في المسار المحدد أو مجلده الأب: '+str(probe_error))
                    x.close()
                    self.redir('/settings?picked_archive_root='+urllib.parse.quote(raw)+'&success='+urllib.parse.quote('نجح اختبار إنشاء ملف مؤقت وقراءته/حذفه من المسار.'));return
                current_root=resolve_archive_root()
                if os.path.normcase(os.path.realpath(root))!=os.path.normcase(os.path.realpath(current_root)):
                    c2=db()
                    attachment_total=c2.execute('SELECT COUNT(*) FROM attachments').fetchone()[0]
                    c2.close()
                    if attachment_total:
                        x.close()
                        self.redir('/storage/migrate-preview?new_root='+urllib.parse.quote(root,safe=''));return
                if not os.path.exists(root):
                    os.makedirs(root,exist_ok=True)
                if not os.path.isdir(root) or not os.access(root,os.R_OK|os.W_OK): raise ValueError('المجلد غير صالح أو لا تتوفر صلاحية القراءة والكتابة.')
                try:
                    root_setting=os.path.relpath(os.path.realpath(root),os.path.realpath(BASE_DIR))
                except ValueError:
                    root_setting=root
                if root_setting=='..' or root_setting.startswith('..'+os.sep):
                    root_setting=root
                x.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('archive_root_path',?)",(root_setting,))
                x.commit();x.close()
                self.redir('/settings?success='+urllib.parse.quote('تم حفظ مسار الأرشيف. لن تُنقل الملفات القديمة تلقائيًا.'));return
            if path=='/reset/settings':
                if role!='admin':raise ValueError('المدير فقط يستطيع إعادة ضبط الإعدادات.')
                password_row=x.execute('SELECT password_hash,salt FROM users WHERE id=?',(uid,)).fetchone()
                supplied=form.get('admin_password','')
                if not password_row or not hmac.compare_digest(bytes(password_row['password_hash']),hashpw(supplied,bytes(password_row['salt']))):
                    raise ValueError('كلمة مرور المدير غير صحيحة؛ لم تُغيّر الإعدادات.')
                preserved={}
                for key in ('archive_root_path','templates_folder_path'):
                    row=x.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
                    if row: preserved[key]=row['value']
                x.execute('BEGIN IMMEDIATE')
                x.execute('DELETE FROM settings')
                for key,value in DEFAULT_SETTINGS.items():
                    x.execute('INSERT INTO settings(key,value) VALUES(?,?)',(key,value))
                for key,value in preserved.items():
                    x.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(key,value))
                x.execute('DELETE FROM fields_config')
                for key,value in DEFAULT_FIELDS.items():
                    x.execute('INSERT INTO fields_config(internal_name,display_name) VALUES(?,?)',(key,value))
                x.commit();x.close()
                audit(uid,'إعادة ضبط','الإعدادات','', 'إعادة الإعدادات والحقول مع الحفاظ على مساري الأرشيف والنماذج')
                self.redir('/settings?success='+urllib.parse.quote('تمت إعادة ضبط الإعدادات فقط. بقي الأرشيف والملفات والمستخدمون كما هي.'));return
            if path=='/reset/archive':
                if role!='admin':raise ValueError('المدير فقط يستطيع إعادة ضبط الأرشيف.')
                password_row=x.execute('SELECT password_hash,salt FROM users WHERE id=?',(uid,)).fetchone()
                supplied=form.get('admin_password','')
                if not password_row or not hmac.compare_digest(bytes(password_row['password_hash']),hashpw(supplied,bytes(password_row['salt']))):
                    raise ValueError('كلمة مرور المدير غير صحيحة؛ لم يُحذف أي شيء.')
                root=resolve_archive_root()
                if form.get('confirm_reset')!='1' or os.path.normcase(os.path.realpath(os.path.abspath(form.get('confirm_path','').strip())))!=os.path.normcase(os.path.realpath(os.path.abspath(root))):
                    raise ValueError('لم يتطابق تأكيد المسار أو لم يتم تحديد مربع الموافقة؛ لم يُحذف أي شيء.')
                if os.path.exists(root) and not os.path.isdir(root):
                    raise ValueError('مسار الأرشيف موجود لكنه ليس مجلدًا؛ لم يُحذف أي شيء.')
                pending_root=None
                stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                # Create a database safety copy and a ZIP of the active archive before any destructive reset.
                database_backup=create_database_safety_backup()
                archive_backup=''
                if os.path.isdir(root):
                    archive_backup=root+'.before_reset_'+stamp+'.zip'
                    while os.path.exists(archive_backup):
                        archive_backup=root+'.before_reset_'+stamp+'_'+uuid.uuid4().hex[:6]+'.zip'
                    try:
                        with zipfile.ZipFile(archive_backup,'w',zipfile.ZIP_DEFLATED) as backup_zip:
                            for walk_root,dirs,names in os.walk(root,followlinks=False):
                                dirs[:]=[d for d in dirs if not os.path.islink(os.path.join(walk_root,d))]
                                for name in names:
                                    source=os.path.join(walk_root,name)
                                    if os.path.islink(source) or not os.path.isfile(source):
                                        continue
                                    backup_zip.write(source,os.path.relpath(source,root))
                        with zipfile.ZipFile(archive_backup,'r') as check_zip:
                            if check_zip.testzip() is not None:
                                raise ValueError('تعذر التحقق من سلامة نسخة الأرشيف الاحتياطية.')
                    except Exception as backup_error:
                        try:
                            if os.path.isfile(archive_backup): os.remove(archive_backup)
                        except Exception: pass
                        raise ValueError('فشلت النسخة الاحتياطية؛ لم يُحذف الأرشيف: '+str(backup_error))
                if os.path.isdir(root):
                    pending_root=root+'.reset_pending_'+stamp
                    while os.path.exists(pending_root):
                        pending_root=root+'.reset_pending_'+stamp+'_'+uuid.uuid4().hex[:6]
                    os.rename(root,pending_root)
                try:
                    x.execute('BEGIN IMMEDIATE')
                    for table in ('record_links','attachments','records','units','storage_places'):
                        x.execute('DELETE FROM '+table)
                    x.commit();x.close()
                except Exception:
                    try: x.rollback();x.close()
                    except Exception: pass
                    if pending_root and os.path.isdir(pending_root):
                        os.rename(pending_root,root)
                    raise
                if pending_root and os.path.isdir(pending_root):
                    try:
                        shutil.rmtree(pending_root)
                    except Exception as delete_error:
                        audit(uid,'إعادة ضبط جزئية','الأرشيف','',str(pending_root))
                        self.redir('/settings?error='+urllib.parse.quote('تم تفريغ سجلات الأرشيف، لكن تعذر حذف المجلد المرحّل: '+pending_root+' — '+str(delete_error)));return
                audit(uid,'إعادة ضبط شاملة','الأرشيف',root,'حذف السجلات والوحدات وأماكن التخزين ومجلد النسخ الرقمية')
                self.redir('/settings?success='+urllib.parse.quote('اكتملت إعادة ضبط الأرشيف. حُفظت نسخة قاعدة البيانات: '+database_backup+(archive_backup and '؛ ونسخة ملفات الأرشيف: '+archive_backup or '؛ لم يكن مجلد الأرشيف موجودًا')+'. بقي البرنامج والمستخدمون والإعدادات ونماذج المراسلات.'));return
            if path=='/settings/login-ui':
                if role!='admin':raise ValueError('المدير فقط.')
                color=form.get('login_marquee_color','#16a085').strip()
                if not re.fullmatch(r'#[0-9A-Fa-f]{6}',color):
                    raise ValueError('اختر لونًا صالحًا لواجهة الدخول.')
                values={
                    'organization_name':form.get('organization_name','').strip(),
                    'login_title':form.get('login_title','').strip(),
                    'welcome_message':form.get('welcome_message','').strip(),
                    'login_marquee':form.get('login_marquee','').strip(),
                    'login_marquee_color':color
                }
                if not values['organization_name'] or not values['login_title'] or not values['welcome_message']:
                    raise ValueError('اسم الجهة وعنوان الدخول ورسالة الترحيب مطلوبة.')
                for key,value in values.items():
                    x.execute('UPDATE settings SET value=? WHERE key=?',(value,key))
                x.commit();x.close();audit(uid,'تخصيص','واجهة الدخول','','حفظ النصوص والألوان')
                self.redir('/settings?success='+urllib.parse.quote('تم حفظ وتطبيق إعدادات واجهة الدخول.'));return
            if path=='/settings/texts':
                if role!='admin':raise ValueError('المدير فقط.')
                for k in DEFAULT_SETTINGS:
                    if k.startswith('show_'):continue
                    if k in form:x.execute('UPDATE settings SET value=? WHERE key=?',(form[k],k))
                for k in ['location_help','number_help','subject_help','date_help','notes_help']:
                    x.execute('UPDATE settings SET value=? WHERE key=?',('1' if form.get('show_'+k) else '0','show_'+k))
                x.commit();x.close();self.redir('/settings?success='+urllib.parse.quote('تم حفظ إعدادات النصوص.'));return
            if path=='/settings/fields':
                if role!='admin':raise ValueError('المدير فقط.')
                for k in DEFAULT_FIELDS:
                    if form.get(k):x.execute('UPDATE fields_config SET display_name=? WHERE internal_name=?',(form[k],k))
                x.commit();x.close();self.redir('/settings?success='+urllib.parse.quote('تم حفظ أسماء الحقول.'));return
            if path=='/users/delete':
                if role!='admin':raise ValueError('المدير فقط.')
                target=form.get('id','')
                if not target:raise ValueError('معرّف المستخدم مفقود.')
                if str(target)==str(uid):raise ValueError('لا يمكن حذف المستخدم الذي سجل الدخول حالياً.')
                target_user=x.execute('SELECT id,username,role FROM users WHERE id=?',(target,)).fetchone()
                if not target_user:raise ValueError('المستخدم غير موجود.')
                if target_user['role']=='admin':
                    admins=x.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
                    if admins<=1:raise ValueError('لا يمكن حذف آخر مدير في النظام.')
                x.execute('DELETE FROM sessions WHERE user_id=?',(target,))
                x.execute('DELETE FROM users WHERE id=?',(target,))
                x.commit();x.close();audit(uid,'حذف','مستخدم',target,target_user['username']);self.redir('/settings?success='+urllib.parse.quote('تم حذف المستخدم.'));return
            if path=='/settings/password':
                pw=form.get('new_password','');
                if len(pw)<4:raise ValueError('كلمة المرور يجب أن تحتوي على 4 أحرف على الأقل.')
                salt=os.urandom(32);x.execute('UPDATE users SET password_hash=?,salt=?,must_change_password=0 WHERE id=?',(hashpw(pw,salt),salt,uid));x.commit();x.close();audit(uid,'تعديل','مستخدم',uid,'كلمة المرور');self.redir('/settings?success='+urllib.parse.quote('تم تغيير كلمة المرور.'));return
            if path=='/users/add':
                if role!='admin':raise ValueError('المدير فقط.')
                un=form.get('username','');pw=form.get('password','');rr=form.get('role','entry')
                if not un or len(pw)<4:raise ValueError('اسم المستخدم وكلمة المرور مطلوبان (4 أحرف على الأقل).')
                if x.execute('SELECT 1 FROM users WHERE username=?',(un,)).fetchone(): raise ValueError('اسم المستخدم موجود مسبقاً. اختر اسم مستخدم آخر.')
                salt=os.urandom(32);x.execute('INSERT INTO users(username,password_hash,salt,role) VALUES(?,?,?,?)',(un,hashpw(pw,salt),salt,'admin' if rr=='admin' else 'entry'));x.commit();x.close();audit(uid,'إضافة','مستخدم',un,rr);self.redir('/settings?success='+urllib.parse.quote('تمت إضافة المستخدم.'));return
            x.close();self.send_error(404)
        except sqlite3.IntegrityError:
            try:x.close()
            except:pass
            target=selected_pdf_error_target(form) if path=='/print/selected-pdfs' else ('/storage/structure' if path=='/storage/structure/create' else ('/templates' if path in ('/templates/path','/templates/open') else ('/settings' if path in ('/users/add','/users/delete','/settings/password','/settings/login-ui','/storage/root','/storage/structure/create','/storage/migrate-attachments','/reset/settings','/reset/archive','/storage/delete','/structure/add','/structure/edit-main','/structure/edit-unit','/main-units/delete','/units/delete') else path)))
            self.redir(target+'?error='+urllib.parse.quote('السجل موجود مسبقاً أو العملية مرتبطة ببيانات أخرى.'))
        except ValueError as e:
            try:x.close()
            except:pass
            target=selected_pdf_error_target(form) if path=='/print/selected-pdfs' else ('/storage/structure' if path=='/storage/structure/create' else ('/templates' if path in ('/templates/path','/templates/open') else ('/settings' if path in ('/users/add','/users/delete','/settings/password','/settings/login-ui','/storage/root','/storage/structure/create','/storage/migrate-attachments','/reset/settings','/reset/archive','/storage/delete','/structure/add','/structure/edit-main','/structure/edit-unit','/main-units/delete','/units/delete') else ('/records/view?id='+urllib.parse.quote(form.get('record_id','')) if path in ('/attachments/add','/attachments/delete') and form.get('record_id') else path))))
            self.redir(target+'?error='+urllib.parse.quote(str(e)))
        except Exception as e:
            try:x.close()
            except:pass
            target=selected_pdf_error_target(form) if path=='/print/selected-pdfs' else path
            self.redir(target+'?error='+urllib.parse.quote('حدث خطأ: '+str(e)))

    def log_message(self,fmt,*args): safe_console_print('[HTTP]',fmt%args)

def _acquire_single_instance_lock():
    """Acquire an OS-level lock so repeated launcher clicks do not spawn copies."""
    global SINGLE_INSTANCE_LOCK_HANDLE
    lock_path=os.path.join(BASE_DIR,'.digital_archive_pro.lock')
    handle=None
    try:
        handle=open(lock_path,'a+b')
        handle.seek(0,os.SEEK_END)
        if handle.tell()==0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        SINGLE_INSTANCE_LOCK_HANDLE=handle
        return True
    except (OSError,IOError):
        if handle:
            try: handle.close()
            except Exception: pass
        return False

def _read_single_instance_port():
    try:
        with open(os.path.join(BASE_DIR,'.digital_archive_pro.lock'),'r') as handle:
            value=handle.read(16).strip()
        port=int(value)
        return port if 8080<=port<=8099 else None
    except Exception:
        return None

def _write_single_instance_port(port):
    if SINGLE_INSTANCE_LOCK_HANDLE is None:
        return
    try:
        handle=SINGLE_INSTANCE_LOCK_HANDLE
        handle.seek(0)
        handle.truncate()
        handle.write(str(int(port)).encode('ascii'))
        handle.flush()
    except Exception:
        pass

def _open_chrome_or_default(url):
    """Launch Chrome in an isolated app-owned profile; never take over personal Chrome."""
    global CHROME_PROCESS, CHROME_PROFILE_DIR
    candidates=[]
    if os.name=='nt':
        chrome_on_path=shutil.which('chrome.exe') or shutil.which('chrome')
        if chrome_on_path:
            candidates.append(chrome_on_path)
        for root in (os.environ.get('PROGRAMFILES'),os.environ.get('PROGRAMFILES(X86)'),
                     os.environ.get('PROGRAMW6432'),os.environ.get('LOCALAPPDATA')):
            if root:
                candidates.append(os.path.join(root,'Google','Chrome','Application','chrome.exe'))
    else:
        for command in ('google-chrome','google-chrome-stable','chromium','chromium-browser'):
            executable=shutil.which(command)
            if executable:
                candidates.append(executable)
    seen=set()
    for executable in candidates:
        if not executable:
            continue
        key=os.path.normcase(os.path.abspath(executable))
        if key in seen or not os.path.isfile(executable):
            continue
        seen.add(key)
        try:
            CHROME_PROFILE_DIR=os.path.join(BASE_DIR,'.digital_archive_chrome_profile_'+uuid.uuid4().hex)
            os.makedirs(CHROME_PROFILE_DIR,exist_ok=True)
            CHROME_PROCESS=subprocess.Popen(
                [executable,'--user-data-dir='+CHROME_PROFILE_DIR,'--no-first-run',
                 '--no-default-browser-check','--disable-background-mode','--new-window',url],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,close_fds=True)
            return True
        except Exception:
            CHROME_PROCESS=None
            CHROME_PROFILE_DIR=None
            continue
    try:
        return bool(webbrowser.open(url,new=1))
    except Exception:
        return False











def _archive_import_scan(root, apply=False, selected_years=None):
    """Preview or restore PDFs from root/year/storage/unit/(وارد|صادر)/filename.pdf."""
    root=os.path.realpath(resolve_archive_root(root))
    if not os.path.isdir(root):
        return {'created_records':0,'linked_files':0,'already_linked':0,'conflicts':0,'missing':0,'details':['المجلد غير موجود؛ لم تُجرَ تغييرات.']}
    c=db()
    details=[]
    try:
        unit_rows=c.execute("SELECT u.id,u.name,s.name AS storage_name FROM units u LEFT JOIN storage_places s ON s.id=u.storage_place_id").fetchall()
        unit_map={(safe_folder_component(r['storage_name']),safe_folder_component(r['name'])):int(r['id']) for r in unit_rows if r['storage_name']}
        storage_map={safe_folder_component(r['name']):int(r['id']) for r in c.execute("SELECT id,name FROM storage_places").fetchall()}
        discovered_years=set()
        discovered_storages=set()
        discovered_units=set()
        records=c.execute("SELECT id,direction,record_number,record_year,unit_id FROM records").fetchall()
        def norm(v):
            return re.sub(r'[\s_]+','',normalize_digits(str(v or '')).casefold())
        recmap={}
        outgoing_numbers=set()
        for r in records:
            key=(r['direction'],norm(r['record_number']),int(r['record_year']),int(r['unit_id']))
            recmap.setdefault(key,[]).append(int(r['id']))
            if r['direction']=='outgoing':
                outgoing_numbers.add((norm(r['record_number']),int(r['record_year'])))
        linked={os.path.normcase(os.path.realpath(resolve_attachment_path(r['relative_path']))) for r in c.execute('SELECT relative_path FROM attachments').fetchall()}
        groups={}
        selected_years=set(normalize_digits(str(y)) for y in selected_years) if selected_years is not None else None
        for walk_root,dirs,names in os.walk(root,followlinks=False):
            dirs[:]=[d for d in dirs if not d.startswith('.') and not os.path.islink(os.path.join(walk_root,d))]
            if os.path.normcase(os.path.realpath(walk_root))==os.path.normcase(root) and selected_years is not None:
                dirs[:]=[d for d in dirs if normalize_digits(d) in selected_years]
            # Ingest directory structure even if no PDFs exist yet
            rel_dir=os.path.relpath(walk_root,root).split(os.sep)
            if len(rel_dir)>=3:
                y_str_d=normalize_digits(rel_dir[0]).strip()
                if re.fullmatch(r'\d{4}',y_str_d):
                    discovered_years.add(int(y_str_d))
                    raw_sp_d=rel_dir[1]; raw_u_d=rel_dir[2]
                    clean_sp_d=safe_folder_component(raw_sp_d); clean_u_d=safe_folder_component(raw_u_d)
                    discovered_storages.add(raw_sp_d); discovered_units.add((raw_sp_d,raw_u_d))
                    if (clean_sp_d,clean_u_d) not in unit_map and apply:
                        sp_id=storage_map.get(clean_sp_d)
                        if sp_id is None:
                            sp_cur=c.execute("INSERT INTO storage_places(name) VALUES(?)",(raw_sp_d,))
                            sp_id=int(sp_cur.lastrowid); storage_map[clean_sp_d]=sp_id
                        u_cur=c.execute("INSERT INTO units(name,storage_place_id) VALUES(?,?)",(raw_u_d,sp_id))
                        unit_map[(clean_sp_d,clean_u_d)]=int(u_cur.lastrowid)
            for filename in names:
                if filename.startswith('.') or not filename.lower().endswith('.pdf'):
                    continue
                full=os.path.realpath(os.path.join(walk_root,filename))
                if os.path.islink(os.path.join(walk_root,filename)) or not path_is_within(full,root) or not os.path.isfile(full):
                    continue
                rel=os.path.relpath(full,root).split(os.sep)
                if len(rel)<5:
                    details.append('مسار خارج البنية المتوقعة: '+os.path.relpath(full,root)); continue
                year_str=normalize_digits(rel[0]).strip()
                if not re.fullmatch(r'\d{4}',year_str):
                    details.append('مسار خارج البنية المتوقعة: '+os.path.relpath(full,root)); continue
                year_num=int(year_str)
                discovered_years.add(year_num)
                raw_storage=rel[1]; raw_unit=rel[2]
                clean_storage=safe_folder_component(raw_storage); clean_unit=safe_folder_component(raw_unit)
                discovered_storages.add(raw_storage); discovered_units.add((raw_storage,raw_unit))
                direction={'وارد':'incoming','incoming':'incoming','صادر':'outgoing','outgoing':'outgoing'}.get(rel[3])
                if direction is None:
                    details.append('تعذر تحديد الاتجاه: '+os.path.relpath(full,root)); continue
                unit_id=unit_map.get((clean_storage,clean_unit))
                if unit_id is None:
                    if apply:
                        sp_id=storage_map.get(clean_storage)
                        if sp_id is None:
                            sp_cur=c.execute("INSERT INTO storage_places(name) VALUES(?)",(raw_storage,))
                            sp_id=int(sp_cur.lastrowid); storage_map[clean_storage]=sp_id
                        u_cur=c.execute("INSERT INTO units(name,storage_place_id) VALUES(?,?)",(raw_unit,sp_id))
                        unit_id=int(u_cur.lastrowid); unit_map[(clean_storage,clean_unit)]=unit_id
                    else:
                        unit_id=-(len(unit_map)+100); unit_map[(clean_storage,clean_unit)]=unit_id
                item={'path':full,'filename':filename,'stem':norm(os.path.splitext(filename)[0]),'year':year_num,'unit_id':unit_id,'direction':direction}
                groups.setdefault((direction,item['year'],unit_id),[]).append(item)
        planned_create={}
        conflicts=missing=already=0
        for (direction,year,unit_id),items in groups.items():
            mains={}
            for item in items:
                if re.fullmatch(r'\d+',item['stem']):
                    mains.setdefault(item['stem'],[]).append(item)
            for item in items:
                m=re.match(r'^(\d+)',item['stem'])
                if m and m.group(1) not in mains:
                    mains[m.group(1)]=[item]
            for number,main_files in mains.items():
                key=(direction,number,year,unit_id)
                current=recmap.get(key,[])
                if len(current)>1 or len(main_files)>1:
                    conflicts+=len(main_files)
                    details.append('تعارض في رقم '+number+' لسنة '+str(year)+'؛ لم يُنشأ سجل تلقائي.')
                    continue
                if current:
                    continue
                if direction=='outgoing' and (number,year) in outgoing_numbers:
                    conflicts+=1
                    details.append('رقم صادر موجود في سنة '+str(year)+' بوحدة أخرى: '+number)
                    continue
                planned_create[key]=main_files[0]
                if direction=='outgoing':
                    outgoing_numbers.add((number,year))
        operations=[]
        for (direction,year,unit_id),items in groups.items():
            candidate_keys=[k for k,ids in recmap.items() if k[0]==direction and k[2]==year and k[3]==unit_id and len(ids)==1]
            candidate_keys += [k for k in planned_create if k[0]==direction and k[2]==year and k[3]==unit_id]
            for item in items:
                full_key=os.path.normcase(os.path.realpath(item['path']))
                if full_key in linked:
                    already+=1
                    continue
                matches=[k for k in candidate_keys if item['stem']==k[1] or (k[1] and bool(re.match(r'^'+re.escape(k[1])+(r'(\D|$)' if k[1].isdigit() else r'([\s_\-\.]|$)'), item['stem'])))]
                if not matches:
                    missing+=1
                    details.append('لا يوجد سجل رئيسي واضح للملف: '+item['filename'])
                    continue
                longest=max(len(k[1]) for k in matches)
                matches=[k for k in matches if len(k[1])==longest]
                if len(matches)!=1:
                    conflicts+=1
                    details.append('اسم ملف ملتبس: '+item['filename'])
                    continue
                operations.append({'path':item['path'],'filename':item['filename'],'key':matches[0]})
        if not apply:
            return {'created_records':len(planned_create),'linked_files':len(operations),'already_linked':already,'conflicts':conflicts,'missing':missing,'years_count':len(discovered_years),'storage_count':len(discovered_storages),'units_count':len(discovered_units),'details':details[:100]}
        created=linked_count=0
        try:
            if not getattr(c, 'in_transaction', False): c.execute('BEGIN IMMEDIATE')
            ids_by_key={k:ids[0] for k,ids in recmap.items() if len(ids)==1}
            for key,item in planned_create.items():
                direction,number,year,unit_id=key
                found=c.execute('SELECT id FROM records WHERE direction=? AND record_number=? AND record_year=? AND unit_id=?',(direction,number,year,unit_id)).fetchall()
                if len(found)==1:
                    ids_by_key[key]=int(found[0]['id']); continue
                if found or (direction=='outgoing' and c.execute("SELECT id FROM records WHERE direction='outgoing' AND record_number=? AND record_year=?",(number,year)).fetchone()):
                    conflicts+=1; continue
                location=effective_location(c,unit_id) or ''
                note='استيراد ذكي من ملفات الأرشيف.'
                cur=c.execute("INSERT INTO records(direction,kind,record_number,record_year,reference,subject,record_date,unit_id,physical_location,notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
                              (direction,'mail',number,year,'','','',unit_id,location,note))
                ids_by_key[key]=int(cur.lastrowid); created+=1
            for op in operations:
                rid=ids_by_key.get(op['key'])
                full=os.path.realpath(op['path'])
                if not rid or not os.path.isfile(full) or not path_is_within(full,root):
                    conflicts+=1; continue
                duplicate=False
                for row in c.execute('SELECT relative_path FROM attachments').fetchall():
                    if os.path.normcase(os.path.realpath(resolve_attachment_path(row['relative_path'])))==os.path.normcase(full):
                        duplicate=True; break
                if duplicate:
                    already+=1; continue
                c.execute("INSERT INTO attachments(record_id,original_name,stored_name,relative_path,mime_type,size_bytes) VALUES(?,?,?,?,?,?)",
                          (rid,op['filename'],op['filename'],stored_attachment_path(full),'application/pdf',os.path.getsize(full)))
                linked_count+=1
            c.commit()
        except Exception:
            c.rollback(); raise
        return {'created_records':created,'linked_files':linked_count,'already_linked':already,'conflicts':conflicts,'missing':missing,'years_count':len(discovered_years),'storage_count':len(discovered_storages),'units_count':len(discovered_units),'details':details[:100]}
    finally:
        c.close()

def _open_browser_when_ready(url, timeout=20):
    """Wait for the local HTTP service, then open Chrome (or a safe fallback)."""
    if os.environ.get('DIGITAL_ARCHIVE_NO_BROWSER')=='1':
        return
    def worker():
        try:
            import urllib.request
            deadline=time.time()+timeout
            while time.time()<deadline:
                try:
                    with urllib.request.urlopen(url+'/login',timeout=1) as response:
                        if response.status==200:
                            if not _open_chrome_or_default(url):
                                safe_console_print('تعذر فتح Chrome أو المتصفح الافتراضي. افتح العنوان المحلي يدويًا: '+url)
                            return
                except Exception:
                    time.sleep(0.25)
            safe_console_print('تعذر التأكد من جاهزية الخدمة أو فتح المتصفح تلقائيًا. افتح العنوان المحلي يدويًا: '+url)
        except Exception as e:
            safe_console_print('تعذر فتح المتصفح تلقائيًا: '+str(e))
    threading.Thread(target=worker,name='ArchiveBrowserLauncher',daemon=True).start()

def run_server():
    global PORT, SERVER_INSTANCE, BASE_REMOVABLE_DRIVES
    if not _acquire_single_instance_lock():
        existing_port=_read_single_instance_port()
        if existing_port:
            safe_console_print(f'البرنامج يعمل بالفعل على http://{HOST}:{existing_port}. لن يتم تشغيل نسخة ثانية.')
        else:
            safe_console_print('توجد نسخة أخرى قيد التشغيل أو يجري بدء تشغيلها. لن يتم فتح نسخة ثانية.')
        return
    init_db()
    ensure_security_settings()
    # The watcher monitors local mounted drives only; no network scanning.
    _start_usb_watcher()
    _start_native_security_alarm()
    _start_connectivity_monitor()
    server=None
    last_error=None
    for candidate in range(8080,8100):
        try:
            server=ThreadingHTTPServer((HOST,candidate),H)
            PORT=candidate
            break
        except OSError as e:
            last_error=e
    if server is None:
        raise RuntimeError('تعذر العثور على منفذ محلي متاح بين 8080 و8099. أغلق نسخة أخرى من البرنامج ثم أعد المحاولة. '+str(last_error))
    server.daemon_threads=False
    server.block_on_close=True
    SERVER_INSTANCE=server
    _write_single_instance_port(PORT)
    url=f'http://{HOST}:{PORT}'
    if not SHUTDOWN_IN_PROGRESS.is_set():
        safe_console_print(f'Digital Archive Pro جاهز على العنوان المحلي: {url}')
        _open_browser_when_ready(url)
    else:
        safe_console_print('[SECURITY] Startup cancelled because a security shutdown was requested.')
    try:
        # A security trigger can arrive during startup before SERVER_INSTANCE exists.
        # In that case do not start accepting requests after shutdown was requested.
        if not SHUTDOWN_IN_PROGRESS.is_set():
            server.serve_forever()
    finally:
        server.server_close()
        _close_managed_chrome()
        if SINGLE_INSTANCE_LOCK_HANDLE is not None:
            try: SINGLE_INSTANCE_LOCK_HANDLE.close()
            except Exception: pass


if __name__=='__main__':
    try:
        run_server()
    except Exception as exc:
        try:
            with open(os.path.join(BASE_DIR,'archive_startup.log'),'a',encoding='utf-8') as log:
                log.write(f"{datetime.datetime.now().isoformat()} | {type(exc).__name__}: {exc}\n")
        except Exception:
            pass
        raise
