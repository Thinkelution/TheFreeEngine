#!/usr/bin/env bash
# Put the new app.css live and add the boot loader to the live index.html, then re-stamp versions. Run with sudo.
set -e
W=/opt/freeengine/current/web
install -o root -g root -m 644 /tmp/app.css $W/app.css
grep -q 'class="boot"' $W/index.html || sed -i 's#<main id="view" tabindex="-1"><!--SEO-START-->#<main id="view" tabindex="-1"><div class="boot" aria-hidden="true"><span class="mark"><b></b><b></b><b></b><b></b></span></div><!--SEO-START-->#' $W/index.html
grep -c 'class="boot"' $W/index.html
bash /tmp/stamp_assets.sh
rm -f /tmp/app.css /tmp/stamp_assets.sh
