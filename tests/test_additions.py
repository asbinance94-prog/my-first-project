import sys, json, os; sys.path.insert(0,".")
import email_fetch as EF, watch_pages as WP
fails=[]
def c(l,x,d=""):
    print(("  PASS  " if x else "  FAIL  ")+l+(f"  <- {d}" if d and not x else ""))
    if not x: fails.append(l)

print("[1] تنظيف HTML للنشرات البريدية")
nl = """<html><head><style>p{color:red}</style></head><body>
<script>track()</script>
<h1>The Rundown AI</h1>
<p>ByteDance released <b>Seedance 2.0</b> today with 60-second clips.</p>
<a href="https://seed.bytedance.com/x">Read more</a>
<div>Unsubscribe</div>&nbsp;&amp;
</body></html>"""
t = EF.clean(nl)
c("السكربت أُزيل", "track()" not in t)
c("الأنماط أُزيلت", "color:red" not in t)
c("اسم النموذج بقي", "Seedance 2.0" in t, t[:90])
c("الرابط حُفظ كنص", "seed.bytedance.com/x" in t, t[:120])
c("الكيانات فُكّت", "&nbsp;" not in t and "&amp;" not in t)

print("\n[2] استخراج عنوان المرسل")
import email as E
m = E.message_from_string('From: "Ben\'s Bites" <ben@bensbites.com>\n\nx')
c("الاسم يُستخرج من From", EF.sender_name(m)=="Ben's Bites", EF.sender_name(m))
m2 = E.message_from_string('From: news@thebatch.ai\n\nx')
c("بلا اسم يسقط على الدومين", EF.sender_name(m2)=="news", EF.sender_name(m2))

print("\n[3] رصد الصفحات — كشف أسطر الإصدار")
page = """<html><body><script>var a=1</script>
<h2>Introducing Higgsfield Cinema Studio 2.1</h2>
<p>Now available for all users.</p>
<p>We use cookies to improve your experience.</p>
<p>Sign in to your account</p>
<p>© 2026 All rights reserved</p>
<p>Random paragraph about nothing in particular here.</p>
<h3>Kling 3.0 update rolling out this week</h3>
</body></html>"""
txt = WP.to_text(page)
cands = WP.candidates(txt)
joined = " | ".join(cands)
c("التقط سطر الإصدار", any("Cinema Studio 2.1" in x for x in cands), joined)
c("التقط سطر التحديث", any("Kling 3.0" in x for x in cands), joined)
c("استبعد الكوكيز", not any("cookie" in x.lower() for x in cands), joined)
c("استبعد تسجيل الدخول", not any("sign in" in x.lower() for x in cands), joined)
c("استبعد حقوق النشر", not any("rights reserved" in x.lower() for x in cands), joined)
c("استبعد الفقرة العادية", not any("Random paragraph" in x for x in cands), joined)

print("\n[4] كشف الصفحات التي تحتاج متصفحاً")
spa = "<html><body><div id='root'></div><script src='app.js'></script></body></html>"
c("صفحة SPA فارغة تُكتشف", len(WP.to_text(spa)) < WP.MIN_TEXT, str(len(WP.to_text(spa))))

print("\n[5] الدمج في fetch.py")
os.makedirs("data", exist_ok=True)
json.dump({"items":[{"fp":"e1","title":"OpenAI ships Sora 3","body":"b","link":"#",
  "published_iso":"2026-08-10T08:00:00+00:00","published":"08:00:00",
  "source_name":"The Rundown","source_weight":7,"track":"newsletter",
  "norm":"openai ships sora"}],"errors":["خطأ تجريبي"]}, open("data/email_items.json","w"))
json.dump({"items":[{"fp":"w1","title":"Introducing Higgsfield Cinema Studio 2.1",
  "body":"b","link":"#","published_iso":"2026-08-10T09:00:00+00:00","published":"09:00:00",
  "source_name":"Higgsfield (رصد صفحة)","source_weight":9,"track":"watch","unverified":True,
  "norm":"introducing higgsfield cinema studio"}],"errors":[]}, open("data/watch_items.json","w"))
src = open("fetch.py").read()
c("fetch.py يقرأ email_items", "email_items.json" in src)
c("fetch.py يقرأ watch_items", "watch_items.json" in src)
c("أخطاء المسارات الجديدة تُسجَّل", 'errors.extend' in src)

print("\n[6] المسار العربي لا يزاحم الأصلي")
import enrich
ar  = {"relevance":9,"source_weight":4,"cluster_count":1,"_recency":1,"category":"video_models","track":"arabic"}
en  = {"relevance":9,"source_weight":9,"cluster_count":3,"_recency":1,"category":"video_models","track":"general"}
c("خبر عربي عالي الأثر يبقى تحت الإنجليزي",
  enrich.score(ar) < enrich.score(en), f"ar={enrich.score(ar)} en={enrich.score(en)}")
c("لكنه لا يُصفَّر تماماً", enrich.score(ar) > 0, str(enrich.score(ar)))

print("\n[7] السقوط الآمن بلا اعتماد")
for k in ("MAIL_USER","MAIL_PASSWORD"): os.environ.pop(k, None)
EF.main()
d=json.load(open("data/email_items.json"))
c("بلا اعتماد بريد يكتب ملفاً فارغاً لا ينهار", d["items"]==[] and "skipped" in d)

print("\n"+("كل الاختبارات نجحت" if not fails else f"فشل {len(fails)}: {fails}"))
sys.exit(1 if fails else 0)
