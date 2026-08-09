#!/usr/bin/env python3
"""
email_fetch.py — يلتقط النشرات البريدية التي لا توفر RSS.

يغطي: The Batch، Import AI، Anthropic News، The Rundown، Superhuman،
Ben's Bites — وأي نشرة تصل بالبريد.

الطريقة: IMAP لا Gmail API. السبب: IMAP يحتاج سطراً واحداً وكلمة مرور
تطبيق، بينما Gmail API يحتاج OAuth ورموز تحديث وملف اعتماد. أصغر حل ينجح.

الإعداد مرة واحدة:
 1. أنشئي إيميل مخصص أو ليبل في بريدك.
 2. فعّلي التحقق بخطوتين في حساب Google.
 3. أنشئي App Password من myaccount.google.com/apppasswords
 4. اشتركي في النشرات بهذا البريد، وأنشئي فلتر يضعها تحت ليبل AI-Newsletters
 5. أضيفي في GitHub Secrets:
      MAIL_USER      البريد
      MAIL_PASSWORD  كلمة مرور التطبيق (16 حرفاً)
      MAIL_FOLDER    اسم الليبل، افتراضياً AI-Newsletters

يكتب: data/email_items.json  ← يقرأه fetch.py ويدمجه في نفس العنقدة
"""

import email
import hashlib
import imaplib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from email.header import decode_header

import requests

DATA = "data"
MODEL = "claude-sonnet-4-6"
MAX_EMAILS = 15            # سقف لكل تشغيل
MAX_STORIES = 4            # قصص تُستخرج من كل عدد
LOOKBACK_HOURS = 26
BODY_CHARS = 9000
BUDGET_USD = 0.25
PRICE_IN, PRICE_OUT = 3.00, 15.00

EXTRACT_SYSTEM = """أنت محرر يستخرج الأخبار من نشرة بريدية تقنية.

من النص المُعطى، استخرجي أهم القصص الإخبارية فقط. تجاهلي تماماً:
الإعلانات، الرعايات، روابط الاشتراك وإلغائه، الترويج لدورات أو منتجات
الناشر، أقسام "أدوات اليوم" الترويجية، والتذييل.

لكل قصة أعيدي كائناً:
- title: عنوان القصة بالإنجليزية كما ورد أو كما يُفهم من النص.
  **يجب أن يحمل اسم الشركة أو النموذج أو الأداة كما ورد.**
- text: جملتان إلى ثلاث بالإنجليزية تصف ما حدث، بالأرقام والأسماء الواردة.
- link: الرابط الأصلي للقصة إن وُجد في النص، وإلا نص فارغ.

قواعد:
- لا تخترعي شيئاً غير موجود في النص.
- إن لم تجدي قصصاً إخبارية حقيقية (عدد ترويجي بحت)، أعيدي مصفوفة فارغة.
- الحد الأقصى {n} قصص، الأهم أولاً.
- أعيدي مصفوفة JSON خالصة بلا نص قبلها أو بعدها وبلا markdown."""


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


def clean(html: str) -> str:
    """يزيل السكربتات والأنماط والوسوم ويحوّل الروابط إلى نص مقروء."""
    s = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", html)
    s = re.sub(r"(?is)<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
               lambda m: f"{re.sub(r'<[^>]+>', '', m.group(2))} [{m.group(1)}]", s)
    s = re.sub(r"(?i)<(br|/p|/div|/tr|/h[1-6])[^>]*>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&nbsp;|&#160;", " ", s)
    s = re.sub(r"&amp;", "&", s)
    s = re.sub(r"&[a-z]{2,8};|&#\d+;", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def decode_subject(raw) -> str:
    if not raw:
        return "نشرة"
    parts = []
    for txt, enc in decode_header(raw):
        if isinstance(txt, bytes):
            parts.append(txt.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(txt)
    return " ".join(parts).strip()


def body_of(msg) -> str:
    plain, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if "attachment" in str(part.get("Content-Disposition", "")):
                continue
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                text = payload.decode(part.get_content_charset() or "utf-8",
                                      errors="replace")
            except Exception:
                continue
            if part.get_content_type() == "text/plain" and not plain:
                plain = text
            elif part.get_content_type() == "text/html" and not html:
                html = text
    else:
        try:
            html = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            html = ""

    # النص الصريح أفضل، لكن كثير من النشرات ترسله فارغاً أو مبتوراً
    if len(plain.strip()) > 400:
        return plain.strip()
    return clean(html)


def extract(newsletter: str, subject: str, text: str, api_key: str):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": 2000,
              "system": EXTRACT_SYSTEM.format(n=MAX_STORIES),
              "messages": [{"role": "user",
                            "content": f"النشرة: {newsletter}\nالعنوان: {subject}\n\n{text[:BODY_CHARS]}"}]},
        timeout=150)
    r.raise_for_status()
    d = r.json()
    out = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    u = d.get("usage", {})
    cost = u.get("input_tokens", 0)/1e6*PRICE_IN + u.get("output_tokens", 0)/1e6*PRICE_OUT
    a, b = out.find("["), out.rfind("]")
    if a == -1 or b == -1:
        raise ValueError("لا مصفوفة JSON في الرد")
    return json.loads(out[a:b+1]), cost


def sender_name(msg) -> str:
    frm = msg.get("From", "")
    m = re.match(r'\s*"?([^"<]+?)"?\s*<', frm)
    return (m.group(1) if m else frm.split("@")[0]).strip() or "نشرة"


def main():
    user = os.environ.get("MAIL_USER")
    pw = os.environ.get("MAIL_PASSWORD")
    folder = os.environ.get("MAIL_FOLDER", "AI-Newsletters")
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not (user and pw):
        print("MAIL_USER أو MAIL_PASSWORD غير موجودين — تخطّي مسار البريد.")
        save(f"{DATA}/email_items.json", {"items": [], "skipped": "بلا اعتماد بريد"})
        return
    if not api_key:
        print("ANTHROPIC_API_KEY غير موجود — لا يمكن استخراج القصص.")
        save(f"{DATA}/email_items.json", {"items": [], "skipped": "بلا مفتاح API"})
        return

    seen = load(f"{DATA}/email_seen.json", {})
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=LOOKBACK_HOURS)).strftime("%d-%b-%Y")

    try:
        M = imaplib.IMAP4_SSL("imap.gmail.com")
        M.login(user, pw)
        status, _ = M.select(f'"{folder}"', readonly=True)
        if status != "OK":
            raise RuntimeError(f"تعذّر فتح الليبل {folder}")
        _, ids = M.search(None, f'(SINCE "{since}")')
    except Exception as exc:
        print(f"فشل الاتصال بالبريد: {exc}")
        save(f"{DATA}/email_items.json", {"items": [], "error": str(exc)})
        return

    msg_ids = ids[0].split()[-MAX_EMAILS:]
    print(f"{len(msg_ids)} رسالة في آخر {LOOKBACK_HOURS} ساعة.")

    items, cost, errors = [], 0.0, []

    for mid in msg_ids:
        try:
            _, data = M.fetch(mid, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])
        except Exception as exc:
            errors.append(f"fetch {mid}: {exc}")
            continue

        subject = decode_subject(msg.get("Subject"))
        name = sender_name(msg)
        key = hashlib.sha256(f"{name}|{subject}".encode()).hexdigest()[:20]
        if key in seen:
            continue

        text = body_of(msg)
        if len(text) < 300:
            seen[key] = now.isoformat()
            continue

        if cost >= BUDGET_USD:
            errors.append("توقف عند حد ميزانية البريد")
            break

        try:
            stories, c = extract(name, subject, text, api_key)
            cost += c
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}")
            print(f"  [فشل] {name} — {exc}")
            continue

        try:
            dt = email.utils.parsedate_to_datetime(msg.get("Date"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            dt = now

        for s in stories[:MAX_STORIES]:
            title = (s.get("title") or "").strip()
            if not title:
                continue
            link = (s.get("link") or "").strip() or "#"
            norm = " ".join(w for w in re.sub(r"[^\w\s]", " ", title.lower()).split()
                            if len(w) > 2)
            items.append({
                "fp": hashlib.sha256(f"{norm}|newsletter".encode()).hexdigest()[:20],
                "title": title[:300],
                "body": (s.get("text") or "")[:1200],
                "link": link,
                "published_iso": dt.astimezone(timezone.utc).isoformat(),
                "published": dt.astimezone(timezone.utc).strftime("%H:%M:%S"),
                "source_name": name,
                "source_weight": 7,
                "track": "newsletter",
                "norm": norm,
            })

        seen[key] = now.isoformat()
        print(f"  [{name}] {len(stories)} قصة — {subject[:50]}")

    try:
        M.close(); M.logout()
    except Exception:
        pass

    limit = (now - timedelta(days=60)).isoformat()
    seen = {k: v for k, v in seen.items() if v > limit}
    save(f"{DATA}/email_seen.json", seen)
    save(f"{DATA}/email_items.json", {
        "fetched_at": now.isoformat(), "items": items,
        "cost_usd": round(cost, 5), "errors": errors,
    })
    print(f"\n{len(items)} قصة من البريد · {cost:.4f}$")


if __name__ == "__main__":
    main()
