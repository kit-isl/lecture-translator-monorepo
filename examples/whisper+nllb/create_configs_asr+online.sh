#!/bin/bash

if [ -z "$1" ]; then
  configs_path=configs
else
  configs_path=$1
fi

# Store different settings to seperate files in configs/ folder
mkdir -p ${configs_path}
rm ${configs_path}/*.sh

# Evaluate ASR

#dataname="IWSLT2019-test-en-de"
dataname="IWSLT2014-test-de-en"

asr_server="whisper" # url defined in run_evaluate_pipeline.sh
declare -a LA2_chunk_sizes=( 0.5 1 2 3 )
declare -a resendings=( "True" "False" )

config_counter=0

for resending in ${resendings[@]}; do
    for LA2_chunk_size in ${LA2_chunk_sizes[@]}; do
	if [[ $resending == "True" ]]; then
	    ASRmode="SendUnstable"
	else
	    ASRmode="SendStable"
	fi

	echo "dataname=${dataname}
	task=ASR
	segmenter=VAD
	ffmpeg_speed=1
	version=online
	stability_detection=True
	ASRmode=${ASRmode}
	asr_server=${asr_server}
	resending=${resending}
	LA2_chunk_size=${LA2_chunk_size}" >> ${configs_path}/config${config_counter}.sh

	config_counter=$((config_counter+1))
    done
done
