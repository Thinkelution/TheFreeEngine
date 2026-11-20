"""Fill the crawlable voice list in web/index.html and write sitemap.xml + robots.txt.
Usage: make_seo.py <voices/catalog.json> <web dir>"""
import datetime as dt
import html
import json
import sys
from pathlib import Path

cat = json.loads(Path(sys.argv[1]).read_text())
web = Path(sys.argv[2])
G = {"f": "female", "m": "male"}
parts = []
for v in cat["voices"]:
    moods = [m for m in v.get("moods", {}) if m != "neutral"]
    bits = [G.get(v.get("gender"), ""), v.get("tone", ""), (v.get("pitch_band") or "") + " pitch" if v.get("pitch_band") else ""]
    desc = ", ".join(b for b in bits if b)
    parts.append(f"<b>{html.escape(v['id'].title())}</b> ({html.escape(desc)}"
                 + (f"; moods: {html.escape(', '.join(moods))}" if moods else "") + ")")
idx = web / "index.html"
page = idx.read_text(encoding="utf-8").replace("<!--SEO-VOICES-->", "; ".join(parts) + ".")
idx.write_text(page, encoding="utf-8")

today = dt.date.today().isoformat()
(web / "sitemap.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://thefreeengine.site/</loc><lastmod>{today}</lastmod><changefreq>weekly</changefreq><priority>1.0</priority></url>
</urlset>
""", encoding="utf-8")
(web / "robots.txt").write_text("""User-agent: *
Allow: /
Disallow: /api/
Disallow: /auth/
Allow: /v1/docs

Sitemap: https://thefreeengine.site/sitemap.xml
""", encoding="utf-8")
print(f"seo: {len(parts)} voices listed")
