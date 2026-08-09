#!/usr/bin/env python3
"""
validate_feeds.py — يفحص كل رابط في sources.json قبل بناء الواجهة.

لماذا هذا الملف موجود:
روابط RSS تموت بصمت. مواقع كثيرة ألغت الفيدات في آخر سنتين، والسبب الأول
لفشل مشاريع تجميع الأخبار هو رابط خاطئ لا أحد فحصه. لا تبني الواجهة
على أي مصدر لم يمر من هنا.

التشغيل:
    pip install feedparser requests
    python validate_feeds.py

المخرجات:
    validated_sources.json  ->  المصادر العاملة فقط
    validation_report.md    ->  تقرير بما نجح وما فشل ولماذا
"""

import json
import sys
from datetime import datetime, timezone

try:
    import feedparser
    import requests
except ImportError:
    sys.exit("pip install feedparser requests")

UA = "Mozilla/5.0 (compatible; AIRadar/1.0; +manara-academy)"
TIMEOUT = 20


def check(url: str) -> dict:
    """يرجع حالة الفيد: هل يستجيب، هل فيه عناصر، وكم عمر آخر عنصر."""
    out = {"url": url, "ok": False, "http": None, "items": 0,
           "latest": None, "age_days": None, "error": None}
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
        out["http"] = r.status_code
        if r.status_code != 200:
            out["error"] = f"HTTP {r.status_code}"
            return out

        parsed = feedparser.parse(r.content)
        out["items"] = len(parsed.entries)

        if not parsed.entries:
            out["error"] = "استجاب لكن بدون عناصر — غالباً ليس فيد RSS"
            return out

        e = parsed.entries[0]
        out["latest"] = getattr(e, "title", "")[:90]

        ts = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        if ts:
            pub = datetime(*ts[:6], tzinfo=timezone.utc)
            out["age_days"] = (datetime.now(timezone.utc) - pub).days

        out["ok"] = True
        return out

    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out


def collect(data: dict) -> list:
    """يجمع كل مصدر له رابط فيد محتمل من كل المسارات."""
    found = []
    for track_key, track in data.items():
        if not isinstance(track, dict):
            continue
        for src in track.get("sources", []):
            method = src.get("method", "rss")
            if method == "email":
                continue
            if src.get("url", "").startswith("http"):
                found.append((track_key, src))
    return found


def main():
    import glob
    targets = []
    for path in sorted(glob.glob("sources*.json")):
        with open(path, encoding="utf-8") as f:
            targets += collect(json.load(f))
        print(f"قرأت {path}")
    print(f"فحص {len(targets)} مصدر...\n")

    results, alive = [], []
    for track, src in targets:
        name = src.get("name", src.get("id", "?"))
        res = check(src["url"])
        res.update(track=track, name=name,
                   claimed_confidence=src.get("confidence", "unlabeled"))
        results.append(res)

        if res["ok"]:
            stale = res["age_days"] is not None and res["age_days"] > 30
            mark = "متوقف؟" if stale else "سليم"
            print(f"  [{mark}] {name} — {res['items']} عنصر، آخر تحديث قبل {res['age_days']} يوم")
            if not stale:
                alive.append({**src, "_track": track})
        else:
            print(f"  [فاشل ] {name} — {res['error']}")

    ok = sum(1 for r in results if r["ok"])
    print(f"\nالنتيجة: {ok} من {len(results)} تعمل، {len(alive)} منها حديثة.\n")

    with open("validated_sources.json", "w", encoding="utf-8") as f:
        json.dump(alive, f, ensure_ascii=False, indent=2)

    lines = [
        "# تقرير فحص المصادر",
        f"\nتاريخ الفحص: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"\nتعمل: **{ok}/{len(results)}** — حديثة (آخر 30 يوم): **{len(alive)}**\n",
        "| المصدر | المسار | الحالة | عناصر | عمر آخر عنصر | ملاحظة |",
        "|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda x: (not x["ok"], x["name"])):
        status = "يعمل" if r["ok"] else "فاشل"
        age = f"{r['age_days']} يوم" if r["age_days"] is not None else "—"
        note = r["error"] or (r["latest"] or "")
        lines.append(f"| {r['name']} | {r['track']} | {status} | {r['items']} | {age} | {note} |")

    lines.append("\n## الخطوة التالية\n")
    lines.append("- كل مصدر **فاشل**: افتحي صفحته، ابحثي في الكود عن `application/rss+xml` وخذي الرابط الصحيح من وسم `<link rel=\"alternate\">`.")
    lines.append("- لو ما فيه وسم أصلاً، الموقع ألغى RSS — انقليه إلى قائمة البريد.")
    lines.append("- كل مصدر **عمر آخر عنصر فيه أكثر من 30 يوم**: احذفيه، ميت فعلياً.")

    with open("validation_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("كُتب: validated_sources.json + validation_report.md")


if __name__ == "__main__":
    main()
