#!/usr/bin/env bash
L=~/sa3-test/bench2.log
awk '/Fatal Python error/{f=1} f' "$L" | grep -a 'File ' | grep -v threading | head -24 | cut -c1-190
echo ---
~/sa3-test/venv/bin/pip list 2>/dev/null | grep -iE 'triton|xformers|torchao|numba|bitsandbytes|einx|k-diffusion|torch'
