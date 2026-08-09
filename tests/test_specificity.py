import sys, json; sys.path.insert(0,".")
import enrich, importlib; importlib.reload(enrich)
fails=[]
def c(l,x,d=""):
    print(("  PASS  " if x else "  FAIL  ")+l+(f"  <- {d}" if d and not x else "")); 
    if not x: fails.append(l)

print("[1] حارس التحديد")
orig = "ByteDance releases Seedance 2.0 with native audio"
good = {"title_ar":"ByteDance تطلق Seedance 2.0 بصوت أصلي","summary_ar":"...","entities":["Seedance 2.0"]}
bad  = {"title_ar":"شركة صينية تطلق نموذج فيديو جديد","summary_ar":"نموذج جديد للفيديو.","entities":[]}
mid  = {"title_ar":"شركة تطلق نموذجاً","summary_ar":"النموذج اسمه Seedance 2.0.","entities":[]}
ar_src = "إطلاق نموذج عربي جديد"

c("عنوان يحمل الأسماء يمر", enrich.specificity(good, orig))
c("عنوان عام بلا أسماء يُرفض", not enrich.specificity(bad, orig))
c("اسم في الملخص وحده يكفي", enrich.specificity(mid, orig))
c("مصدر عربي بلا لاتينية لا يُعاقب", enrich.specificity(bad, ar_src))

print("\n[2] المسار الكامل مع نموذج يعمّم")
import os; os.environ["ANTHROPIC_API_KEY"]="t"
json.dump({"runs":[],"spend":{}},open("data/health.json","w"))
json.dump({"items":[]},open("data/news.json","w"))
json.dump({"fetched_at":"2026-08-10T09:00:00+00:00","stats":{"sources_total":2,"sources_failed":0},"errors":[],
 "items":[{"fp":"f1","title":"ByteDance releases Seedance 2.0","body":"b","link":"https://x.com/1",
  "published_iso":"2026-08-10T08:00:00+00:00","published":"08:00:00","source_name":"S","source_weight":9,
  "cluster_count":1,"sources":[{"name":"S","url":"https://x.com/1"}],"norm":"bytedance releases seedance"}]},
 open("data/raw_new.json","w"))
enrich.call_claude = lambda items,k,a=1: ([{"id":i["fp"],"title_ar":"شركة تطلق نموذج فيديو",
  "summary_ar":"وصف عام.","category":"video_models","relevance":9,"content_angle":"","entities":[]} for i in items], 0.001)
enrich.main()
d=json.load(open("data/news.json")); it=d["items"][0]
c("العنوان المعمَّم عُلِّم needs_review", it["needs_review"], str(it.get("needs_review")))
c("العنوان الأصلي محفوظ دائماً", it["title_original"]=="ByteDance releases Seedance 2.0", it.get("title_original"))
c("العدّاد سُجّل في health", json.load(open("data/health.json"))["runs"][0]["generic_titles"]==1)

print("\n[3] الواجهة تعرض الأصل")
h=open("index.html").read()
c("الواجهة تعرض title_original", 'class="orig"' in h and "it.title_original" in h)
c("الواجهة تعرض شارة entities", 'class="ent"' in h)
c("شارة المراجعة موجودة", "بلا أسماء" in h)
c("موجز النسخ يضم العنوان الأصلي", "it.title_original ? '   ('" in h)

print("\n"+("كل الاختبارات نجحت" if not fails else f"فشل {len(fails)}: {fails}"))
sys.exit(1 if fails else 0)
