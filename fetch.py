#!/usr/bin/env python3
"""
fetch.py — يسحب الفيدات المفحوصة ويكتب العناصر الجديدة فقط.

يقرأ:  validated_sources.json  (مخرج validate_feeds.py)
يكتب:  data/state.json   حالة كل فيد (ETag / Last-Modified)
       data/seen.json    بصمات العناصر المعالجة سابقاً
       data/raw_new.json المدخل الوحيد لـ enrich.py

مبادئ:
- فشل فيد واحد لا يوقف التشغيل إطلاقاً.
- لا يُعاد تحميل فيد لم يتغيّر (توفير وقت وحصة).
- كل عنصر يُعالَج مرة واحدة في حياته.
"""

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta

import feedparser
import requests

DATA = "data"
UA = "Mozilla/5.0 (compatible; AIRadar/1.0; +manara-academy)"
TIMEOUT = 25
MAX_PER_FEED = 12          # سقف لكل فيد في التشغيل الواحد
MAX_TOTAL = 60             # سقف كلي — حاجز تكلفة
SEEN_TTL_DAYS = 45         # عمر البصمات قبل التنظيف
ITEM_MAX_AGE_DAYS = 4      # لا تُعالَج عناصر أقدم من هذا


# ---------- أدوات ----------

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


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"&[a-z]+;|&#\d+;", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_title(t: str) -> str:
    """تطبيع للمقارنة: حروف صغيرة، بلا ترقيم، بلا كلمات وظيفية."""
    t = (t or "").lower()
    t = re.sub(r"[^\w\s]", " ", t)
    stop = {"the", "a", "an", "of", "for", "to", "in", "on", "and", "with",
            "is", "are", "new", "its", "it", "s", "now", "how", "why"}
    return " ".join(w for w in t.split() if w not in stop and len(w) > 2)


def fingerprint(title: str, link: str) -> str:
    host = re.sub(r"^https?://(www\.)?", "", link or "").split("/")[0]
    return hashlib.sha256(f"{normalize_title(title)}|{host}".encode()).hexdigest()[:20]


def entry_time(e):
    ts = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
    if ts:
        try:
            return datetime(*ts[:6], tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


# ---------- التحليل (قابل للاختبار بلا شبكة) ----------

def parse_feed_bytes(content: bytes, source: dict, now=None) -> list:
    """يحوّل بايتات فيد إلى عناصر خام. دالة نقية — تُختبر بدون إنترنت."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=ITEM_MAX_AGE_DAYS)
    parsed = feedparser.parse(content)
    out = []

    for e in parsed.entries[:MAX_PER_FEED * 3]:
        title = strip_html(getattr(e, "title", ""))
        link = getattr(e, "link", "") or ""
        if not title or not link:
            continue

        pub = entry_time(e)
        if pub < cutoff:
            continue

        body = ""
        if getattr(e, "content", None):
            body = strip_html(e.content[0].get("value", ""))
        if not body:
            body = strip_html(getattr(e, "summary", ""))

        out.append({
            "fp": fingerprint(title, link),
            "title": title[:300],
            "body": body[:1200],
            "link": link,
            "published_iso": pub.isoformat(),
            "published": pub.strftime("%H:%M:%S"),
            "source_name": source.get("name", "?"),
            "source_weight": source.get("weight", 5),
            "track": source.get("_track", "general"),
            "norm": normalize_title(title),
        })
        if len(out) >= MAX_PER_FEED:
            break

    return out


def jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def cluster(items: list, threshold: float = 0.45) -> list:
    """
    يجمع العناصر التي تصف نفس الحدث. تجميع بايثوني بحت:
    مجاني، حتمي، قابل للاختبار، ولا يعتمد على استدعاء API.
    العنصر الممثل هو صاحب أعلى وزن مصدر.
    """
    groups = []
    for it in sorted(items, key=lambda x: -x["source_weight"]):
        placed = False
        for g in groups:
            if jaccard(it["norm"], g[0]["norm"]) >= threshold:
                g.append(it)
                placed = True
                break
        if not placed:
            groups.append([it])

    merged = []
    for g in groups:
        lead = g[0]
        merged.append({
            **lead,
            "cluster_count": len(g),
            "sources": [{"name": m["source_name"], "url": m["link"]} for m in g],
            "member_fps": [m["fp"] for m in g],
        })
    return merged


# ---------- الشبكة ----------

def pull(source: dict, state: dict) -> tuple:
    url = source["url"]
    st = state.get(url, {})
    headers = {"User-Agent": UA}
    if st.get("etag"):
        headers["If-None-Match"] = st["etag"]
    if st.get("modified"):
        headers["If-Modified-Since"] = st["modified"]

    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
    except Exception as exc:
        return [], f"{type(exc).__name__}"

    if r.status_code == 304:
        return [], None                      # لم يتغيّر — ليس خطأ
    if r.status_code != 200:
        return [], f"HTTP {r.status_code}"

    state[url] = {
        "etag": r.headers.get("ETag"),
        "modified": r.headers.get("Last-Modified"),
        "checked": datetime.now(timezone.utc).isoformat(),
    }
    return parse_feed_bytes(r.content, source), None


# ---------- التشغيل ----------

def main():
    sources = load("validated_sources.json", None)
    if not sources:
        sys.exit("لا يوجد validated_sources.json — شغّلي validate_feeds.py أولاً.")

    state = load(f"{DATA}/state.json", {})
    seen = load(f"{DATA}/seen.json", {})
    now = datetime.now(timezone.utc)

    fresh, errors, unchanged = [], [], 0

    for src in sources:
        items, err = pull(src, state)
        if err:
            errors.append({"source": src.get("name"), "error": err})
            print(f"  [فشل] {src.get('name')} — {err}")
            continue
        if not items:
            unchanged += 1
            continue

        new = [i for i in items if i["fp"] not in seen]
        fresh.extend(new)
        if new:
            print(f"  [جديد] {src.get('name')} — {len(new)}")
        time.sleep(0.4)                      # تهذيب تجاه الخوادم

    # ضمّ مصادر البريد ورصد الصفحات — نفس شكل العنصر، نفس العنقدة
    for extra, label in ((f"{DATA}/email_items.json", "البريد"),
                         (f"{DATA}/watch_items.json", "رصد الصفحات")):
        blob = load(extra, {})
        got = [i for i in blob.get("items", []) if i.get("fp") not in seen]
        if got:
            print(f"  [{label}] {len(got)} عنصر")
            fresh.extend(got)
        if blob.get("errors"):
            errors.extend({"source": label, "error": e} for e in blob["errors"])

    # الأحدث أولاً، ثم قص عند السقف الكلي
    fresh.sort(key=lambda x: x["published_iso"], reverse=True)
    capped = fresh[:MAX_TOTAL]
    dropped = len(fresh) - len(capped)

    merged = cluster(capped)

    # تسجيل كل ما دخل المعالجة حتى لا يتكرر
    stamp = now.isoformat()
    for m in merged:
        for fp in m["member_fps"]:
            seen[fp] = stamp

    # تنظيف البصمات القديمة
    limit = now - timedelta(days=SEEN_TTL_DAYS)
    seen = {k: v for k, v in seen.items()
            if v > limit.isoformat()}

    save(f"{DATA}/state.json", state)
    save(f"{DATA}/seen.json", seen)
    save(f"{DATA}/raw_new.json", {
        "fetched_at": stamp,
        "items": merged,
        "stats": {
            "sources_total": len(sources),
            "sources_unchanged": unchanged,
            "sources_failed": len(errors),
            "items_new": len(fresh),
            "items_dropped_by_cap": dropped,
            "clusters": len(merged),
            "tracks": sorted({i.get("track","general") for i in merged}),
        },
        "errors": errors,
    })

    print(f"\n{len(fresh)} عنصر جديد → {len(merged)} عنقود. "
          f"{unchanged} فيد بلا تغيير، {len(errors)} فشل.")
    if dropped:
        print(f"تحذير: {dropped} عنصر تجاوز السقف ({MAX_TOTAL}) ولم يُعالَج.")


if __name__ == "__main__":
    main()
