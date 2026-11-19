#!/usr/bin/env bash
# Try Stable Audio 3 Small with several thread counts, each in a 6 GB-capped unit. Run with sudo.
cd /home/administrator/sa3-test
systemctl reset-failed 'sa3-*' 2>/dev/null
for n in ${@:-2 4}; do
  systemd-run --wait --quiet --unit=sa3-probe-$n --uid=administrator --gid=administrator \
    -p MemoryMax=6G -p MemorySwapMax=0 -p Nice=15 -p RuntimeMaxSec=600 \
    --setenv=HOME=/home/administrator --setenv=THREADS=$n --setenv=OMP_NUM_THREADS=$n \
    --working-directory=/home/administrator/sa3-test \
    /bin/bash -c "venv/bin/python threads_probe.py > probe$n.log 2>&1"
  echo "threads=$n exit=$? :: $(grep -aE 'threads=|Illegal' probe$n.log | tail -1)"
  systemctl reset-failed sa3-probe-$n 2>/dev/null
done
curl -s -m5 -o /dev/null -w 'freeengine %{http_code}\n' 127.0.0.1:18710/v1/health
