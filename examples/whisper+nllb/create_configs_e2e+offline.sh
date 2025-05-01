#!/bin/bash

if [ -z "$1" ]; then
  configs_path=configs
else
  configs_path=$1
fi

# Store different settings to seperate files in configs/ folder
mkdir -p ${configs_path}
rm ${configs_path}/*.sh

# Evaluate e2e ST

dataname="IWSLT2014-test-de-en"

asr_server="whisper" # url defined in run_evaluate_pipeline.sh

echo "dataname=${dataname}
task=e2eST
segmenter=VAD
ffmpeg_speed=-1
version=offline
stability_detection=False
asr_server=${asr_server}" >> ${configs_path}/config0.sh

