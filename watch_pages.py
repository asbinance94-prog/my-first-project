#!/usr/bin/env python3
"""
watch_pages.py — يراقب صفحات الأدوات التي لا توفر RSS.

يغطي: Higgsfield، Kling، Midjourney، HeyGen، Seedance، Luma.

اقرئي هذا قبل أن تعتمدي عليه:
هذه أضعف حلقة في النظام، وليست بديلاً عن RSS. السبب أن أغلب هذه المواقع
تطبيقات JavaScript تُبنى في المتصفح، والطلب البسيط يرجّع هيكلاً فارغاً.
السكربت يكتشف هذه الحالة ويبلّغك بها صراحة بدل أن يصمت.

الاستراتيجية: يستخرج الأسطر التي تشبه إعلانات الإصدار (تحمل رقم إصدار،
أو كلمات مثل launch/introducing/update)، ويقارن مجموعة الأسطر بما رآه
سابقاً. الأسطر الجديدة فقط تصير أخباراً.

يكتب: data/watch_items.json  ← يقرأه fetch.py
       data/watch_state.json  ← الأسطر المرصودة سابقاً
       data/watch_report.md   ← أي صفحة تحتاج متصفحاً
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone

import requests

DATA = "data"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = 25
MIN_TEXT = 800          # أقل من هذا = الصفحة تحتاج JS غالباً
MAX_NEW_PER_PAGE = 4

TARGETS = [
    {"name": "Higgsfield", "url": "https://higgsfield.ai/", "weight": 9},
    {"name": "Kling AI", "url": "https://klingai.com/", "weight": 8},
    {"name": "Midjourney Updates", "url": "https://updates.midjourney.com/", "weight": 8},
    {"name": "HeyGen", "url": "https://www.heygen.com/", "weight": 6},
    {"name": "Luma AI", "url": "https://lumalabs.ai/", "weight": 6},
]

# ما يشبه إعلان إصدار
SIGNAL = re.compile(
    r"(?i)\b(v?\d+\.\d+|launch(?:ing|ed)?|introduc(?:e|ing)|now available|"
    r"released?|update[ds]?|new model|announc(?:e|ing|ed)|rolling out|beta)\b")
NOISE = re.compile(
    r"(?i)(cookie|privacy|sign in|log in|subscribe|pricing|terms|careers|"
    r"©|all rights|follow us|contact us)")


def load(p, d):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return d


def save(p, o):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    t = p + ".tmp"
    with open(t, "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False, indent=2)
    os.replace(t, p)


def to_text(html: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", html)
    s = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6]|/section)[^>]*>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&nbsp;|&#160;", " ", s)
    s = re.sub(r"&amp;", "&", s)
    s = re.sub(r"&[a-z]{2,8};|&#\d+;", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n{2,}", "\n", s).strip()


def candidates(text: str) -> list:
    """الأسطر التي تشبه إعلان إصدار."""
    out, seen = [], set()
    for line in text.split("\n"):
        line = line.strip()
        if not (12 <= len(line) <= 190):
            continue
        if NOISE.search(line) or not SIGNAL.search(line):
            continue
        key = re.sub(r"\W+", "", line.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


def main():
    state = load(f"{DATA}/watch_state.json", {})
    now = datetime.now(timezone.utc)
    items, needs_js, errors = [], [], []

    for t in TARGETS:
        try:
            r = requests.get(t["url"], headers={"User-Agent": UA}, timeout=TIMEOUT)
            if r.status_code != 200:
                errors.append(f"{t['name']}: HTTP {r.status_code}")
                continue
            text = to_text(r.text)
        except Exception as exc:
            errors.append(f"{t['name']}: {type(exc).__name__}")
            continue

        if len(text) < MIN_TEXT:
            needs_js.append({"name": t["name"], "url": t["url"], "chars": len(text)})
            print(f"  [يحتاج متصفح] {t['name']} — {len(text)} حرف فقط")
            continue

        found = candidates(text)
        known = set(state.get(t["url"], {}).get("lines", []))
        first_run = t["url"] not in state

        new = [ln for ln in found if re.sub(r"\W+", "", ln.lower()) not in known]

        state[t["url"]] = {
            "lines": [re.sub(r"\W+", "", ln.lower()) for ln in found][:400],
            "checked": now.isoformat(),
        }

        if first_run:
            print(f"  [تهيئة] {t['name']} — {len(found)} سطر مرجعي، بلا أخبار")
            continue
        if not new:
            continue

        print(f"  [تغيّر] {t['name']} — {len(new)} سطر جديد")
        for ln in new[:MAX_NEW_PER_PAGE]:
            norm = " ".join(w for w in re.sub(r"[^\w\s]", " ", ln.lower()).split()
                            if len(w) > 2)
            items.append({
                "fp": hashlib.sha256(f"{norm}|{t['name']}".encode()).hexdigest()[:20],
                "title": ln[:300],
                "body": f"سطر جديد رُصد على صفحة {t['name']}. المصدر صفحة لا فيد — تحققي بنفسك قبل النشر.",
                "link": t["url"],
                "published_iso": now.isoformat(),
                "published": now.strftime("%H:%M:%S"),
                "source_name": f"{t['name']} (رصد صفحة)",
                "source_weight": t["weight"],
                "track": "watch",
                "unverified": True,
                "norm": norm,
            })

    save(f"{DATA}/watch_state.json", state)
    save(f"{DATA}/watch_items.json", {
        "fetched_at": now.isoformat(), "items": items,
        "needs_js": needs_js, "errors": errors,
    })

    lines = ["# تقرير رصد الصفحات", f"\n{now.strftime('%Y-%m-%d %H:%M UTC')}\n"]
    if needs_js:
        lines.append("## صفحات تحتاج متصفحاً حقيقياً\n")
        lines.append("هذه تطبيقات JavaScript. الطلب البسيط يرجّع هيكلاً فارغاً.\n")
        lines.append("| الصفحة | حروف مستخرجة |\n|---|---|")
        for n in needs_js:
            lines.append(f"| {n['name']} | {n['chars']} |")
        lines.append("\n**الخيارات:**")
        lines.append("- الأرخص: احذفيها من TARGETS وتابعيها عبر Reddit — "
                     "r/StableDiffusion و r/aivideo يغطيان إصداراتها خلال ساعات.")
        lines.append("- الأثقل: أضيفي Playwright في الـ workflow "
                     "(`pip install playwright && playwright install chromium`) "
                     "واستبدلي requests.get بجلسة متصفح. يضيف ~90 ثانية لكل تشغيل.")
    if errors:
        lines.append("\n## أخطاء\n")
        for e in errors:
            lines.append(f"- {e}")
    if not needs_js and not errors:
        lines.append("كل الصفحات استُخرجت بنجاح بلا متصفح.")

    with open(f"{DATA}/watch_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n{len(items)} سطر جديد · {len(needs_js)} صفحة تحتاج متصفحاً · "
          f"{len(errors)} خطأ")
    if needs_js:
        print("راجعي data/watch_report.md")


if __name__ == "__main__":
    main()
