import os
tpl = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>{ch}</title>{items}</channel></rss>"""
item = """<item><title>{t}</title><link>{l}</link>
<pubDate>{d}</pubDate><description><![CDATA[{b}]]></description></item>"""

from email.utils import format_datetime
from datetime import datetime, timezone, timedelta
now = datetime.now(timezone.utc)
def d(h): return format_datetime(now - timedelta(hours=h))

os.makedirs("tests/fixtures", exist_ok=True)

# ثلاثة مصادر تغطي نفس الحدث بصياغات مختلفة + أخبار مستقلة
a = tpl.format(ch="Alpha", items="".join([
 item.format(t="OpenAI releases Sora 3 with longer coherent shots", l="https://openai.com/a1", d=d(2), b="<p>The new model extends <b>shot length</b> significantly.</p>"),
 item.format(t="Funding round closes for a video startup", l="https://openai.com/a2", d=d(5), b="Series B announced."),
 item.format(t="Ancient news that must be filtered out", l="https://openai.com/a3", d=d(24*9), b="old"),
]))
b = tpl.format(ch="Beta", items="".join([
 item.format(t="Sora 3 released by OpenAI, longer coherent shots supported", l="https://marktechpost.com/b1", d=d(3), b="Coverage of the release."),
 item.format(t="New LoRA training method for character consistency", l="https://marktechpost.com/b2", d=d(6), b="A method paper."),
]))
c = tpl.format(ch="Gamma", items="".join([
 item.format(t="OpenAI ships Sora 3 — longer shots that stay coherent", l="https://theverge.com/c1", d=d(4), b="Consumer angle."),
]))
broken = "<html><body>not a feed at all</body></html>"

for n, s in [("alpha.xml",a),("beta.xml",b),("gamma.xml",c),("broken.html",broken)]:
    open(f"tests/fixtures/{n}","w").write(s)
print("fixtures written")
