#!/usr/bin/env bash
# Add a password-protected, never-cached, never-indexed /review/ area to thefreeengine.site. Run with sudo.
set -e
S=/etc/nginx/sites-available/thefreeengine.site
grep -q 'location \^~ /review/' $S && { echo "already there"; exit 0; }
cp $S /root/thefreeengine.site.before-review
BLOCK='    location ^~ /review/ {\n        alias /srv/freeengine-review/;\n        auth_basic "Private review";\n        auth_basic_user_file /etc/nginx/freeengine-review.htpasswd;\n        autoindex off;\n        add_header Cache-Control "private, no-store" always;\n        add_header X-Robots-Tag "noindex, nofollow" always;\n        add_header Referrer-Policy "no-referrer" always;\n    }'
# insert before the first "listen 443 ssl" line, i.e. inside the HTTPS server block
awk -v b="$BLOCK" '/listen 443 ssl/ && !d { gsub(/\\n/, "\n", b); print b; d=1 } { print }' $S > $S.new && mv $S.new $S
if nginx -t 2>/dev/null; then systemctl reload nginx; echo "review area added"; else cp /root/thefreeengine.site.before-review $S; echo "nginx test FAILED, reverted"; exit 1; fi
