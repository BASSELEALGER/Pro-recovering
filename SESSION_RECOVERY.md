# SESSION_RECOVERY.md — Digital Archive Pro
آخر تحديث: 2026-10-12

هذا الملف هو دليل استئناف العمل إذا انتهت المحادثة أو حدث خطأ. اقرأه مع `PROJECT_TRACKER.md` و`FINAL_AUDIT_REPORT.md` قبل إجراء أي تعديل.

## نقطة العمل
- المستودع: https://github.com/BASSELEALGER/digital-archive-V10-stable-backup
- فرع العمل: `work/digital-archive-pro-verified-base-2026-10-12`
- الالتزام المرجعي الأصلي: `3affecf6a7293bb036514abfb64d20407b0cb344`
- لا تعدّل `main` ولا تدمج الفرع تلقائيًا.
- فرع العمل ليس إصدارًا نهائيًا. لا تستخدم حزمة قديمة كأنها مبنية من أحدث التعديلات.
- تقرير التدقيق: https://github.com/BASSELEALGER/digital-archive-V10-stable-backup/blob/work/digital-archive-pro-verified-base-2026-10-12/FINAL_AUDIT_REPORT.md
- سجل المتطلبات: https://github.com/BASSELEALGER/digital-archive-V10-stable-backup/blob/work/digital-archive-pro-verified-base-2026-10-12/PROJECT_TRACKER.md
- البرومبت المرجعي: https://github.com/BASSELEALGER/digital-archive-V10-stable-backup/blob/work/digital-archive-pro-verified-base-2026-10-12/PROMPT_SAHIQA_MAHIQA.md
- قناة التنفيذ المفضلة: Jules عبر CircleCI عند توفر التنفيذ الفعلي. لا تعتمد على GitHub Actions كمسار تشغيل أو تطلب من المستخدم إعادة تشغيله.

## المتطلبات التي لا يجوز نسيانها
1. هيكل الأرشيف: **السنة ← التخزين ← الوحدة ← الوارد / الصادر**.
2. قسم نماذج Word مستقل: نماذج حسب الوحدة أو عام/مشترك، وتصنيفات إدارية ومالية وشهادات وقرارات. عند استبدال نموذج، الجديد هو النشط، والقديم نسخة احتياطية فقط لا تظهر كنموذج نشط؛ لا تتأثر بقية النماذج.
3. موضوع الوارد/الصادر اختياري تمامًا؛ اختبر الواجهة والخادم وحفظ سجل بموضوع فارغ.
4. الطريقة التقليدية لإنشاء الوارد/الصادر ورفع PDF تبقى كما هي.
5. دمج PDF Linking داخل البرنامج كأداة مستقلة وظيفيًا. الإدخال PDF واحد أو مجموعة؛ المطابقة من **اسم الملف فقط** ولا يُقرأ محتوى PDF. البحث في الوارد والصادر عبر جميع السنوات والوحدات دون تحديد السنة. عرض النتائج في جدول مراجعة واحد؛ لا ربط نهائي دون موافقة المستخدم؛ عرض الغموض بدل التخمين. يمكن فك الارتباط لاحقًا، وفك الارتباط لا يحذف الملف أو السجل.
6. تحسين واجهة الإحصاءات مع بقاء الأرقام صحيحة، وتحسين القائمة الجانبية قليلًا.
7. زر إعادة ضبط المصنع: توضيح نطاق الإجراء، تأكيد قبل التنفيذ، حماية ملفات الأرشيف والمرفقات والنسخ الاحتياطية من الحذف غير المقصود، واختبارات فعلية.
8. التشغيل المحلي: انتظر جاهزية الخدمة ثم افتح Chrome، وتعامل مع تعذّر فتحه وتجنب النوافذ/الخوادم المكررة.
9. البصمة: `© Noureddine 2026`.
10. Windows 10 و11 أولوية؛ Windows 7 SP1 ثانوي. استخدم CircleCI/Jules كقناة التنفيذ المتاحة، ولا تعتمد على GitHub Actions كمسار العمل.
11. لا إعلان عن النجاح دون اختبار فعلي، ولا تغيير في `main` قبل المراجعة والموافقة.

## حالة التحقق المعروفة
تشغيلات GitHub Actions السابقة لم توفر دليل اختبار/بناء صالحًا، وبناءً على توجيه المستخدم لا تُكررها كمسار التنفيذ. لا يوجد في سجل الحالة ما يثبت اختبار Windows 7 فعليًا.

## ماذا تفعل في جلسة جديدة؟
1. اقرأ هذا الملف و`PROJECT_TRACKER.md` و`FINAL_AUDIT_REPORT.md`.
2. افحص آخر commit على فرع العمل، وحالة Jules/CircleCI المتاحة فعليًا، وملفات الاختبارات والتغييرات.
3. حدّد ما هو منفذ وما هو مختبر وما هو معتمد، كل حالة على حدة.
4. استأنف من أول بند غير مكتمل؛ لا تبدأ من الصفر ولا تعدّل `main`.
5. حدّث سجل المتابعة بعد كل مرحلة مهمة، بما في ذلك الروابط الفعلية للاختبارات وArtifacts وبصمات SHA-256 إذا توفرت.

## نص جاهز لنسخه إلى محادثة جديدة
«واصل مشروع Digital Archive Pro من دون البدء من الصفر. ابدأ بقراءة SESSION_RECOVERY.md وPROJECT_TRACKER.md وFINAL_AUDIT_REPORT.md في فرع work/digital-archive-pro-verified-base-2026-10-12 داخل BASSELEALGER/digital-archive-V10-stable-backup. تحقق من آخر commit ونتائج Actions قبل أي تعديل. حافظ على main دون تغيير، والتزم بكل المتطلبات في سجل المشروع، خصوصًا نماذج Word، وموضوع المراسلة الاختياري، وإعادة ضبط المصنع، وفتح Chrome بعد جاهزية الخادم، وتحسين الإحصاءات، ودمج PDF Linking الذي يعتمد على أسماء ملفات PDF فقط مع تأكيد المستخدم وفك ارتباط آمن، مع إبقاء الرفع التقليدي كما هو. أعطِ الأولوية لـ Windows 10/11 واجعل Windows 7 ثانويًا، وجرّب CircleCI. لا تدّعِ نجاح اختبار أو توفر EXE إلا بعد التحقق الفعلي. إذا احتجت مني شيئًا، اطلب تحديدًا رابط تشغيل أو سجل أو صورة خطأ أو ملف الحزمة؛ لا تطلب مني إعادة شرح المشروع كله.» 

## إذا ظهر خطأ واحتجت مني معلومات
أرسل ما يتوفر من التالي فقط:
- رابط تشغيل GitHub Actions أو CircleCI الفاشل.
- صورة رسالة الخطأ كاملة أو نصها.
- سجل الخطوات (logs) إن أمكن.
- اسم الملف الذي فشل أو ملف ZIP الناتج.
- الخطوات التي قمت بها قبل ظهور المشكلة.
لا ترسل كلمات مرور أو رموز وصول أو قاعدة بيانات تحتوي بيانات شخصية. لا حاجة لإعادة شرح المتطلبات المحفوظة هنا.

## تعليمات تشغيل ملزمة — تحديث 2026-10-12
- لا تجعل GitHub Actions قناة التنفيذ الأساسية، ولا تكرر إعادة تشغيل وظائفه بوصفها تقدمًا. المستخدم أوضح أن Actions غير متاحة له كمسار عمل. اعتمد Jules عبر CircleCI عندما تكون قناة CircleCI متاحة فعلًا، وراجع الكود مباشرة بالتوازي. لا تدّعِ تشغيل CircleCI أو التحكم في جلسة Jules ما لم يظهر تنفيذ فعلي يمكن التحقق منه.
- لا تتوقف عند إنشاء جلسة أو كتابة خطة أو تحديث ملف استئناف؛ المطلوب تغييرات برمجية فعلية، واختبارات قابلة للتنفيذ، وبناء حزمة حقيقية.
- عند التعارض بين هذا الملف ونسخة أقدم من البرومبت، يُعتمد هذا الملف وآخر طلب صريح للمستخدم. لذلك PDF Linking ضمن نطاق العمل حسب المواصفات الواردة أعلاه؛ العبارة القديمة في البرومبت التي تستبعدها قديمة ويجب تصحيحها حتى لا تربك Jules.
- لا تطلب من المستخدم إعادة سرد المتطلبات، ولا تعتمد على تذكيره بالقيود. لا تعدّل `main`، ولا تفتح PR، ولا تدمج الفروع.

## سجل التنفيذ المباشر — 2026-10-12
- تم تصحيح تعارض نطاق PDF Linking بين ملف الاستئناف والبرومبت: الأحدث وطلب المستخدم السابق يجعلانها ضمن نطاق المشروع، والبرومبت تم تحديثه ليطابق ذلك.
- تم تصحيح اسم الفرع في README وسجل المتابعة من الاسم القديم `work/digital-archive-pro-audit` إلى الفرع المعتمد.
- تعديل برمجي مباشر في `app.py` بالالتزام `24557d36f752e88206ecd44223f9fe05c410c56c`: بعد جاهزية خدمة HTTP المحلية، يحاول البرنامج فتح Google Chrome أولًا على Windows/Linux، ثم يستخدم المتصفح الافتراضي كبديل، ويطبع رسالة يدوية عند فشل الفتح.
- أضيفت اختبارات انحدار لهذا السلوك في `tests/test_safety_guards.py` بالالتزام `122c5a02313d0e06060553599ba62a17161bdf54`.
- تم فحص صياغة helper والاختبارين نحويًا، واجتاز helper فحصين مركزين بمُحاكاة تشغيل Chrome والرجوع للمتصفح الافتراضي. لم تُشغّل مجموعة اختبارات التطبيق أو التكامل، ولم يُنفّذ بناء؛ لا تعتمد التعديل للإصدار قبل تشغيل الاختبارات والبناء عبر بيئة المشروع فعلًا.


## Resume checkpoint — 2026-10-13
- Repository: BASSELEALGER/digital-archive-V10-stable-backup
- Work branch only: work/digital-archive-pro-verified-base-2026-10-12
- Changes added during this continuation: optional filename-only auto-link at startup/every 60 seconds; settings UI for PDF folder/toggle; smart archive restore with year selection, explicit confirmation and duplicate-safe behavior; PDF health statistics; password-protected resets and retained database/archive backups; added but unexecuted QA cases.
- Required next actions: fetch current branch HEAD; inspect the final diff; run python -m py_compile app.py and python tests/qa_smoke.py in a local checkout; repair failures; then build and test Windows x86/x64 artifacts. Do not use GitHub Actions, Jules or CircleCI. Do not modify main, and do not claim release readiness before real test evidence.


## Latest guarded checkpoint — 2026-10-13
- Latest application changes include year discovery that accepts Arabic-digit folder names, year-selectable smart restore, PDF health statistics, PDF auto-link configuration in Settings, and password-protected resets with retained backups.
- Latest QA source includes tests for PDF settings persistence, one-time auto-link behavior, smart restore idempotence, reset password rejection, and archive ZIP backup integrity.
- Latest source/test commits before this note: app.py commit 35a0849204b988c76af328ec35ed174e84260e57; tests/qa_smoke.py commit 3f086be512d53877e4aa52c9d6acca8fd2efd8db.
- None of the newly added tests have been executed. The working branch is still not release-ready; obtain local syntax/QA output and fresh Windows builds before packaging.
