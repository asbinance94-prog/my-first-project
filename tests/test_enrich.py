import json, sys, re
sys.path.insert(0,".")
import enrich

fails=[]
def check(l,c,d=""):
    print(("  PASS  " if c else "  FAIL  ")+l+(f"  <- {d}" if d and not c else ""))
    if not c: fails.append(l)

print("[1] استخراج JSON من ردود فوضوية")
cases = [
 '[{"id":"a"}]',
 '```json\n[{"id":"a"}]\n```',
 'تفضلي النتيجة:\n[{"id":"a"}]\nانتهى',
 '```\n[{"id":"a"}]\n```',
]
for i,c in enumerate(cases):
    try:
        check(f"صيغة {i+1}", enrich.extract_json(c)==[{"id":"a"}])
    except Exception as e:
        check(f"صيغة {i+1}", False, str(e))
try:
    enrich.extract_json("لا يوجد شيء هنا"); check("رد بلا JSON يرفع خطأ", False)
except ValueError: check("رد بلا JSON يرفع خطأ", True)

print("\n[2] مسار الإثراء الكامل (استدعاء مُحاكى)")
raw = json.load(open("data/raw_new.json"))
def fake(items, key, attempt=1):
    out=[]
    for it in items:
        vid = "sora" in it["title"].lower()
        out.append({"id":it["fp"],
          "title_ar":"إطلاق نموذج فيديو بلقطات أطول" if vid else "خبر آخر",
          "summary_ar":"وصف. المعنى العملي: أثر مباشر على الإنتاج.",
          "category":"video_models" if vid else "business",
          "relevance":9 if vid else 2,
          "content_angle":"مقارنة قبل/بعد" if vid else ""})
    return out, 0.0031
enrich.call_claude = fake
import os; os.environ["ANTHROPIC_API_KEY"]="test-key"
json.dump({"runs":[],"spend":{}}, open("data/health.json","w"))
json.dump({"items":[]}, open("data/news.json","w"))
enrich.main()

d=json.load(open("data/news.json"))
top=d["items"][0]
check("الخبر عالي الأثر تصدّر", top["category"]=="video_models", top["category"])
check("مضاعف نماذج الفيديو طُبّق (score>7)", top["score"]>7, f"score={top['score']}")
check("لا شيء بقي بلا ترجمة", not any(i["untranslated"] for i in d["items"]))
check("التكلفة سُجّلت", "$" in d["health"] and d["health"].split("·")[-1].strip()!="0.000$", d["health"])
h=json.load(open("data/health.json"))
check("الإنفاق اليومي تراكم", list(h["spend"].values())[0]>0, str(h["spend"]))

print("\n[3] حاجز الميزانية")
h["spend"][list(h["spend"])[0]] = 99.0
json.dump(h, open("data/health.json","w"))
json.dump({"items":[]}, open("data/news.json","w"))
enrich.main()
d2=json.load(open("data/news.json"))
check("عند تجاوز الميزانية تُكتب الأخبار خاماً ولا تُفقد",
      len(d2["items"])==3 and all(i["untranslated"] for i in d2["items"]),
      f"{len(d2['items'])} عنصر")

print("\n[4] عقد البيانات مع index.html")
html=open("index.html").read()
used = set(re.findall(r"it\.([a-z_]+)", html)) | set(re.findall(r"DATA\.([a-z_]+)", html))
top_keys={"generated_at","sample","health","items"}
item_keys=set(d["items"][0].keys())
missing_top = {k for k in used if k in top_keys} - set(d.keys())
missing_item = {k for k in used if k not in top_keys and k!="items"} - item_keys
check("كل حقول المستوى الأعلى موجودة", not missing_top, str(missing_top))
check("كل حقول العنصر التي تقرأها الواجهة موجودة", not missing_item, str(missing_item))
check("sources مصفوفة كائنات فيها name و url",
      all({"name","url"}<=set(s) for s in d["items"][0]["sources"]))

print("\n"+("كل الاختبارات نجحت" if not fails else f"فشل {len(fails)}: {fails}"))
sys.exit(1 if fails else 0)
