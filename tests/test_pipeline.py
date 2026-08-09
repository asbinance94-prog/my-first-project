import json, sys
sys.path.insert(0, ".")
from fetch import parse_feed_bytes, cluster, fingerprint, normalize_title, jaccard, strip_html

FX = "tests/fixtures"
srcs = [
    {"name":"Alpha","url":"x","weight":9,"_track":"general"},
    {"name":"Beta","url":"y","weight":8,"_track":"general"},
    {"name":"Gamma","url":"z","weight":7,"_track":"general"},
]
files = ["alpha.xml","beta.xml","gamma.xml"]

items = []
for f, s in zip(files, srcs):
    got = parse_feed_bytes(open(f"{FX}/{f}","rb").read(), s)
    print(f"  {s['name']}: {len(got)} عنصر")
    items += got

fails = []
def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  <- {detail}" if detail and not cond else ""))
    if not cond: fails.append(label)

print("\n[1] التحليل")
check("العناصر القديمة (>4 أيام) مُستبعدة", len(items) == 5, f"got {len(items)}")
check("HTML مُنظّف من النص", "<b>" not in items[0]["body"], items[0]["body"][:60])
check("التوقيت مستخرج", all(len(i["published"])==8 for i in items))

print("\n[2] الفيد المكسور")
bad = parse_feed_bytes(open(f"{FX}/broken.html","rb").read(), srcs[0])
check("HTML غير فيد يعطي صفر عناصر بلا استثناء", bad == [], str(bad)[:60])

print("\n[3] البصمة")
f1 = fingerprint("OpenAI releases Sora 3!", "https://openai.com/a1")
f2 = fingerprint("The OpenAI releases Sora 3", "https://www.openai.com/a1?utm=x")
f3 = fingerprint("Something completely different", "https://openai.com/a1")
check("عنوان شبه متطابق + نفس الدومين = نفس البصمة", f1 == f2, f"{f1} vs {f2}")
check("عنوان مختلف = بصمة مختلفة", f1 != f3)

print("\n[4] العنقدة")
merged = cluster(items)
sora = [m for m in merged if "sora" in m["norm"]]
check("خبر Sora من 3 مصادر صار عنقوداً واحداً", len(sora)==1 and sora[0]["cluster_count"]==3,
      f"clusters={len(sora)} count={sora[0]['cluster_count'] if sora else 0}")
check("ممثل العنقود هو الأعلى وزناً (Alpha=9)", sora and sora[0]["source_name"]=="Alpha",
      sora[0]["source_name"] if sora else "-")
check("كل المصادر الثلاثة محفوظة بروابطها", sora and len(sora[0]["sources"])==3)
check("الأخبار المستقلة لم تُدمج خطأً", len(merged)==3, f"total clusters={len(merged)}")

print("\n[5] عتبة التشابه")
check("عنوانان مختلفان لا يتجاوزان العتبة",
      jaccard(normalize_title("New LoRA training method for character consistency"),
              normalize_title("Funding round closes for a video startup")) < 0.45)

print("\n[6] بنية المخرج لـ enrich.py")
need = {"fp","title","body","link","published_iso","published","source_name","source_weight","cluster_count","sources"}
check("كل الحقول المطلوبة موجودة", need <= set(merged[0].keys()), str(need - set(merged[0].keys())))

json.dump({"fetched_at":"2026-08-10T09:00:00+00:00","items":merged,
           "stats":{"sources_total":3,"sources_failed":0},"errors":[]},
          open("data/raw_new.json","w"), ensure_ascii=False, indent=2)

print("\n" + ("كل الاختبارات نجحت" if not fails else f"فشل {len(fails)}: {fails}"))
sys.exit(1 if fails else 0)
