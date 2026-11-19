#!/usr/bin/env bash
# Re-stamp ?v=<hash> on app.js / app.css links in the live index.html (after a hot file update). Run with sudo.
W=/opt/freeengine/current/web
for f in app.js app.css; do
  v=$(sha256sum "$W/$f" | cut -c1-10)
  sed -i -E "s#/$f(\?v=[0-9a-f]+)?\"#/$f?v=$v\"#" "$W/index.html"
done
grep -oE '/app\.(js|css)\?v=[0-9a-f]+' "$W/index.html"
