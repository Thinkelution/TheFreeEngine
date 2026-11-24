#!/usr/bin/env bash
# Set the /review/ password. The password comes on stdin. Run with sudo.
set -e
IFS= read -r P
P=${P%$'\r'}
[ ${#P} -ge 12 ] || { echo "password too short"; exit 1; }
H=$(printf '%s' "$P" | openssl passwd -apr1 -stdin)
printf 'dan-review:%s\n' "$H" > /etc/nginx/freeengine-review.htpasswd
chown root:www-data /etc/nginx/freeengine-review.htpasswd
chmod 640 /etc/nginx/freeengine-review.htpasswd
cut -d: -f1 /etc/nginx/freeengine-review.htpasswd; cut -d: -f2 /etc/nginx/freeengine-review.htpasswd | cut -c1-6
