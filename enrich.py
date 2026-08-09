#!/usr/bin/env python3
"""
enrich.py — يحوّل العناقيد الخام إلى أخبار عربية مرتّبة.

يقرأ:  data/raw_new.json
يكتب:  data/news.json    (تقرأه الواجهة)
       data/health.json  (تشغيل، تكلفة، أخطاء)

قواعد التكلفة:
- استدعاء واحد لكل دفعة، لا استدعاء لكل خبر.
- ميزانية يومية صارمة. عند تجاوزها يتوقف الإثراء ويُكتب الخام كما هو.
- أي فشل في الإثراء لا يفقد الخبر — يُكتب بعنوانه الأصلي ويُعلَّم.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

DATA = "data"
MODEL = "claude-sonnet-4-6"
BATCH = 12
KEEP_DAYS = 14
DAILY_BUDGET_USD = 0.60

# تسعير تقريبي لكل مليون توكن — راجعيه، الأسعار تتغيّر
PRICE_IN, PRICE_OUT = 3.00, 15.00

SYSTEM = """أنت محرر أخبار تقنية لمنصة عربية متخصصة في صناعة المحتوى بالذكاء الاصطناعي.
الجمهور: صانعة أفلام ومدربة معتمدة في الخليج، ومتابعوها من المعلمين وصناع المحتوى.

لكل خبر أعد كائناً يحتوي:

- id: نفس المعرّف المُعطى، حرفياً.
- title_ar: عنوان عربي دقيق أقل من 14 كلمة.
  **يجب أن يحتوي اسم الجهة واسم المنتج أو النموذج ورقم الإصدار كما ورد
  في النص، بحروف لاتينية.** ممنوع استبدال الاسم بوصف عام.
  ممنوع: ثوري، يغيّر كل شيء، مذهل، يهز.
- summary_ar: جملتان. الأولى ماذا حدث، **وتذكر الأسماء والأرقام الواردة
  في النص** (مدة، دقة، سعر، رقم إصدار). الثانية تبدأ بـ "المعنى العملي:"
  وتشرح الأثر على من ينتج محتوى بصرياً بالذكاء الاصطناعي.
- entities: مصفوفة بأسماء الجهات والنماذج والأدوات الواردة في النص،
  بحروف لاتينية كما وردت. مثال: ["ByteDance","Seedance 2.0"].
  إن لم يرد أي اسم في النص، أعيديها مصفوفة فارغة.
- category: video_models أو image_models أو audio_voice أو tools أو research
  أو business أو policy أو other.
- relevance: 0 إلى 10. المعيار الوحيد: هل يغيّر هذا طريقة عمل صانع أفلام
  بالذكاء الاصطناعي خلال 30 يوماً؟
  إصدار نموذج فيديو أو صورة = 8 إلى 10.
  تحديث أداة إنتاج = 6 إلى 8.
  ورقة بحثية بلا تطبيق قريب = 3 إلى 5.
  جولة تمويل أو خبر شركات = 1 إلى 3.
- content_angle: إن صلح الخبر لريلز أو منشور تعليمي، اكتبي الزاوية في سطر
  واحد قابل للتنفيذ. وإلا اتركيه نصاً فارغاً.

قواعد صارمة:

1. الأسماء تُنقل حرفياً بحروف لاتينية ولا تُترجم ولا تُعمَّم:
   أسماء الشركات، النماذج، الأدوات، أرقام الإصدارات، والمصطلحات التقنية
   مثل latent space و inference و checkpoint و prompt و LoRA و seed و upscale.

   سيئ:  "شركة تطلق نموذج فيديو جديد بلقطات أطول"
   جيد:  "ByteDance تطلق Seedance 2.0 بلقطات تصل إلى 60 ثانية"

   سيئ:  "تحديث في أداة توليد صور يغيّر طريقة قراءة المراجع"
   جيد:  "Runway تضيف negativePrompt لـ Veo 3.1 عبر الـ API"

   الاسم المحذوف يجعل الخبر بلا قيمة للقارئة. العنوان الذي لا يحمل اسماً
   واحداً على الأقل بحروف لاتينية هو عنوان فاشل.

2. ممنوع اختراع أي رقم أو تاريخ أو اسم غير موجود في النص المُعطى.
   إن لم يذكر النص اسم النموذج، لا تخمّنيه — اكتبي ما ورد فقط.

3. إن كان النص ناقصاً، اكتبي summary_ar بما هو موجود ولا تكملي من عندك.

4. أعيدي مصفوفة JSON خالصة. بلا نص قبلها أو بعدها. بلا علامات markdown."""


LATIN = re.compile(r"[A-Za-z][A-Za-z0-9.\-]{1,}")


def specificity(enriched: dict, original_title: str) -> bool:
    """
    هل حافظ الملخص على الأسماء؟
    القاعدة: إن كان العنوان الأصلي يحمل أسماء لاتينية، فالعنوان العربي
    أو الملخص يجب أن يحمل واحداً منها على الأقل. وإلا فقد الخبر قيمته.
    """
    if not LATIN.search(original_title or ""):
        return True                      # مصدر عربي أصلاً — لا ينطبق
    text = f"{enriched.get('title_ar','')} {enriched.get('summary_ar','')}"
    return bool(LATIN.search(text)) or bool(enriched.get("entities"))


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def extract_json(text: str):
    """يستخرج مصفوفة JSON حتى لو أحاطها النموذج بنص أو أسوار markdown."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("لا توجد مصفوفة JSON في الرد")
    return json.loads(t[start:end + 1])


def call_claude(payload_items, api_key, attempt=1):
    lines = []
    for it in payload_items:
        lines.append(json.dumps({
            "id": it["fp"],
            "title": it["title"],
            "text": it["body"][:900],
            "source": it["source_name"],
        }, ensure_ascii=False))
    user = "الأخبار:\n" + "\n".join(lines)

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 4000,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=180,
    )

    if r.status_code in (429, 500, 529) and attempt <= 3:
        wait = 5 * (2 ** (attempt - 1))
        print(f"    إعادة محاولة بعد {wait}ث (HTTP {r.status_code})")
        time.sleep(wait)
        return call_claude(payload_items, api_key, attempt + 1)

    r.raise_for_status()
    data = r.json()
    text = "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text")
    usage = data.get("usage", {})
    cost = (usage.get("input_tokens", 0) / 1e6 * PRICE_IN
            + usage.get("output_tokens", 0) / 1e6 * PRICE_OUT)
    return extract_json(text), cost


def fallback(it):
    """خبر غير مُثرى — لا يُفقد أبداً، فقط يُعلَّم."""
    return {
        "id": it["fp"],
        "title_ar": it["title"],
        "summary_ar": (it["body"][:200] + "…") if it["body"] else "",
        "category": "other",
        "relevance": min(7, it["source_weight"]),
        "content_angle": "",
        "entities": [],
        "untranslated": True,
        "needs_review": True,
    }


def score(item):
    # المسار العربي مشتق ومتأخر — لا يزاحم الأصلي في "الأهم الآن"
    if item.get("track") == "arabic":
        return round(item.get("relevance", 0) * 0.25, 3)
    return round(
        item.get("relevance", 0) * 0.5
        + item.get("source_weight", 5) * 0.2
        + min(item.get("cluster_count", 1), 6) * 0.2
        + item.get("_recency", 0) * 0.1
        , 3) * (1.5 if item.get("category") in ("video_models", "image_models") else 1.0)


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    raw = load(f"{DATA}/raw_new.json", None)
    if raw is None:
        sys.exit("لا يوجد data/raw_new.json — شغّلي fetch.py أولاً.")

    incoming = raw.get("items", [])
    health = load(f"{DATA}/health.json", {"runs": [], "spend": {}})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    spent_today = health.get("spend", {}).get(today, 0.0)

    enriched_map, cost, api_errors = {}, 0.0, []

    if not api_key:
        api_errors.append("مفتاح ANTHROPIC_API_KEY غير موجود — كُتبت الأخبار بلا ترجمة")
    elif spent_today >= DAILY_BUDGET_USD:
        api_errors.append(f"تجاوز الميزانية اليومية ({DAILY_BUDGET_USD}$) — أُوقف الإثراء")
    else:
        for i in range(0, len(incoming), BATCH):
            if spent_today + cost >= DAILY_BUDGET_USD:
                api_errors.append("توقف عند حد الميزانية أثناء التشغيل")
                break
            chunk = incoming[i:i + BATCH]
            try:
                results, c = call_claude(chunk, api_key)
                cost += c
                for r in results:
                    if isinstance(r, dict) and r.get("id"):
                        enriched_map[r["id"]] = r
                print(f"  دفعة {i//BATCH + 1}: {len(results)} خبر، {c:.4f}$")
            except Exception as exc:
                api_errors.append(f"دفعة {i//BATCH + 1}: {type(exc).__name__}: {exc}")
                print(f"  [فشل] دفعة {i//BATCH + 1} — {exc}")

    # الدمج: كل عنصر خام يجد نظيره المُثرى أو يسقط على fallback
    now = datetime.now(timezone.utc)
    fresh_items, generic = [], 0
    for it in incoming:
        e = enriched_map.get(it["fp"]) or fallback(it)
        if not e.get("untranslated") and not specificity(e, it["title"]):
            e["needs_review"] = True
            generic += 1
        age_h = (now - datetime.fromisoformat(it["published_iso"])).total_seconds() / 3600
        merged = {
            "id": it["fp"],
            "title_ar": e.get("title_ar") or it["title"],
            "summary_ar": e.get("summary_ar", ""),
            "category": e.get("category", "other"),
            "relevance": e.get("relevance", 5),
            "content_angle": e.get("content_angle", ""),
            "untranslated": e.get("untranslated", False),
            "needs_review": e.get("needs_review", False),
            "entities": e.get("entities", []),
            "title_original": it["title"],
            "published": it["published"],
            "published_iso": it["published_iso"],
            "cluster_count": it.get("cluster_count", 1),
            "sources": it.get("sources", []),
            "source_weight": it["source_weight"],
            "track": it.get("track", "general"),
            "unverified": it.get("unverified", False),
            "_recency": max(0.0, 1 - age_h / 48),
        }
        merged["score"] = score(merged)
        fresh_items.append(merged)

    # الدمج مع الأرشيف الحالي
    old = load(f"{DATA}/news.json", {}).get("items", [])
    by_id = {o["id"]: o for o in old}
    for f in fresh_items:
        by_id[f["id"]] = f

    cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
    kept = [v for v in by_id.values() if v.get("published_iso", "") > cutoff]
    kept.sort(key=lambda x: (-x.get("score", 0), x.get("published_iso", "")), reverse=False)
    kept.sort(key=lambda x: -x.get("score", 0))

    stats = raw.get("stats", {})
    save(f"{DATA}/news.json", {
        "generated_at": now.isoformat(),
        "sample": False,
        "health": (f"{stats.get('sources_total',0) - stats.get('sources_failed',0)}"
                   f"/{stats.get('sources_total',0)} مصدر يعمل · "
                   f"{len(fresh_items)} جديد · {cost:.3f}$"),
        "items": kept,
    })

    health.setdefault("spend", {})[today] = round(spent_today + cost, 5)
    health.setdefault("runs", []).insert(0, {
        "at": now.isoformat(),
        "new": len(fresh_items),
        "total": len(kept),
        "cost_usd": round(cost, 5),
        "spent_today_usd": health["spend"][today],
        "feed_errors": raw.get("errors", []),
        "api_errors": api_errors,
        "generic_titles": generic,
        "stats": stats,
    })
    health["runs"] = health["runs"][:100]
    health["spend"] = {k: v for k, v in health["spend"].items()
                       if k >= (now - timedelta(days=35)).strftime("%Y-%m-%d")}
    save(f"{DATA}/health.json", health)

    print(f"\n{len(fresh_items)} خبر جديد · {len(kept)} في اللوحة · "
          f"{cost:.4f}$ · اليوم {health['spend'][today]:.4f}$")
    if generic:
        print(f"  تنبيه: {generic} عنوان فقد الأسماء اللاتينية — راجعي البرومبت لو تكررت.")
    for e in api_errors:
        print(f"  تنبيه: {e}")


if __name__ == "__main__":
    main()
