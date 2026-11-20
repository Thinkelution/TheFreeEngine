#!/usr/bin/env bash
# Quick check of the mood builder on 2 voices with few clips.
export TFE_LIMIT_VOICES=2 TFE_MAX_CLIPS=25 TFE_TRY_PER_MOOD=2
bash /opt/tfe/voices/build_moods.sh
