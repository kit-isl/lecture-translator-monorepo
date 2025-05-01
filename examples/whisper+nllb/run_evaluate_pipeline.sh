#!/bin/bash
set -eu  # Crash if variable used without being set

# Load in the parameter variables
config_params_path=$1
experiment_name=$2
server_url_with_login=$3
server_url_without_login=$(echo "$server_url_with_login" | sed 's/\/\/[^:]*:[^@]*@/\/\//')
source $config_params_path

ARTIFACT_PATH=${ARTIFACT_PATH:-$(pwd)}
data_root_path=${ARTIFACT_PATH}/data
output_root_path=${ARTIFACT_PATH}/output

echo "Artifact root path ${ARTIFACT_PATH}"

src_lang=$(echo ${dataname} | awk -F'-' '{print $(NF-1)}')
tgt_langs=$(echo ${dataname} | awk -F'-' '{print $NF}')

asr_input_lang=${asr_input_lang:-}
if [ -z "${asr_input_lang}" ]; then
  if [[ $task == "e2eST" ]]; then
    asr_input_lang="${src_lang},${tgt_langs}"
    asr_server_url=http://whisper-worker:5000/asr/infer/${src_lang},${tgt_langs}
    asr_server_var="--asr-kv=asr_server_${src_lang},${tgt_langs}=${asr_server_url}"
  elif [[ ($task == "cascadedST") || ($task == "ASR") ]]; then
    asr_input_lang=${src_lang}
    asr_server_url=http://whisper-worker:5000/asr/infer/${src_lang},${src_lang}
    asr_server_var="--asr-kv=asr_server_${src_lang}=${asr_server_url}"
  fi
fi

mt_server=${mt_server:-default}
if [[ $mt_server == "nllb" ]]; then
  IFS=',' read -ra langs <<< "$tgt_langs"
  mt_server_var=""
  for tgt_lang in "${langs[@]}"; do
    mt_server_url=http://nllb-worker:5000/predictions/mt-${src_lang},${tgt_lang}
    mt_server_var="${mt_server_var} --mt-kv=mt_server_${src_lang}-${tgt_lang}=${mt_server_url}"
  done
else
  mt_server_var=""
fi

if [ -z "${LA2_chunk_size:-}" ]; then
  LA2_chunk_size=1
fi

if [ -z "${minPrefixExtension:-}" ]; then
  minPrefixExtension_var=""
else
  minPrefixExtension_var="--mt-kv=minPrefixExtension=${minPrefixExtension}"
fi

if [ -z "${MTpairs:-}" ]; then
  MTpairs=$(echo $tgt_langs | sed "s/\([^,]*\)/$asr_input_lang-\1/g")
fi

use_prep=${use_prep:-False}
if [[ $use_prep == "True" ]]; then
  use_prep_var="--use-prep"
else
  use_prep_var=""
fi

echo "server_url=${server_url_without_login}" >> ${config_params_path}
echo "asr_input_lang=${asr_input_lang}" >> ${config_params_path}

if [ -z "${audio_augmentation:-}" ]; then
  audio_augmentation=None
  processed_data_dir=${dataname}_proccessed
else
  processed_data_dir=${dataname}_proccessed_add${audio_augmentation}
fi

if [ -z "${ffmpeg_speed:-}" ]; then
  ffmpeg_speed=-1
fi

if [ -z "${version:-}" ]; then
  version=offline
fi

if [ -z "${segmenter:-}" ]; then
  segmenter=VAD
fi

if [ -z "${stability_detection:-}" ]; then
  stability_detection=False
fi

if [ -z "${ASRmode:-}" ]; then
  ASRmode=SendStable
fi

if [ -z "${MTmode:-}" ]; then
  MTmode=SendStable
fi

run_id=$(python log_runs.py --function start_run --params_path $config_params_path --experiment_name $experiment_name --local_mlflow_path "http://mlflow:5000")
output_dir=${output_root_path}/${run_id}/${dataname}/${task}


# Fresh start
rm -rf ${output_root_path}/${run_id}/${dataname}
mkdir -p ${output_dir}

{
  mkdir -p ${data_root_path}
  if [[ ! -d "${data_root_path}/${processed_data_dir}" ]]; then
    python CreateTestData.py \
      --dataname ${dataname} \
      --data_root_path ${data_root_path} \
      --processed_data_dir ${processed_data_dir} \
      --src_langs ${src_lang} \
      --tgt_langs ${tgt_langs} \
      --audio_augmentation ${audio_augmentation}
  fi

  # Translate
  timestamp() {
      date '+%s%N' --date="$1"
  }
  start=$(date )
  echo "${start}: start translating"

  # Count the number of audio files
  nr_audio_files=$(ls ${data_root_path}/${processed_data_dir}/SRCAUDIO.${src_lang}/audio_*.wav | wc -l )

  if [[ $task == "MT" ]]; then
    echo "Sending request for translating"
    python StreamTranslation.py \
      -u ${server_url_with_login} \
      --source-file ${data_root_path}/${processed_data_dir}/SRCTEXT.${src_lang}/SRCTEXT.txt \
      --output-file ${output_dir}/raw_output.txt \
      --mt-kv mode=${MTmode} \
      --mt-kv language=${MTpairs} \
      ${minPrefixExtension_var} \
      ${mt_server_var} \
      --verbose 0
  else
    if [[ $task == "cascadedST" ]]; then
      # Set the MT component
      mt_args="--run-mt=${MTpairs} --mt-kv=mode=${MTmode} ${mt_server_var} ${minPrefixExtension_var}"
    else
      # No MT component (task e2eST and ASR)
      mt_args=""
    fi
    # Send each audio file one by one in the correct order
    for ((i=0;i<${nr_audio_files};i++));
    do
      srcaudio_file=${data_root_path}/${processed_data_dir}/SRCAUDIO.${src_lang}/audio_${i}.wav
      srcaudio_file_base_name="$(basename -- $srcaudio_file)"
      sentence_index=$(echo $srcaudio_file_base_name | grep -o -E '[0-9]+')
      echo "Sending request for transcribing audio ${sentence_index}"
      set -x
      python audioclient/client.py \
        -u ${server_url_with_login} \
        -i ffmpeg \
        -f ${srcaudio_file} \
        --ffmpeg-speed ${ffmpeg_speed} \
        --no-textsegmenter \
        --output-file ${output_dir}/audio_${sentence_index}.txt \
        --asr-kv version=${version} --asr-kv segmenter=${segmenter} --asr-kv stability_detection=${stability_detection} \
        --asr-kv mode=${ASRmode} --asr-kv language=${asr_input_lang} \
        --asr-kv LA2_chunk_size=${LA2_chunk_size} \
        ${asr_server_var} \
        ${use_prep_var} \
        --prep-kv version=${version} \
        ${mt_args} \
        --print 2
      set +x
      cat ${output_dir}/audio_${sentence_index}.txt >> ${output_dir}/raw_output.txt
    done
  fi
  end=$(date )
  echo "${end}: end translating"
  echo "Runtime: $(( $(timestamp "$end") - $(timestamp "$start") ))"


  main_metrics_paths=${output_dir}/score.txt
  all_metrics_pahts=( ${main_metrics_paths} )

  if [[ ($task == "cascadedST") || ($task == "e2eST") || ($task == "MT") ]]; then
    IFS=',' read -ra langs <<< "$tgt_langs"
    for tgt_lang in "${langs[@]}"; do
      # Evaluate the main task
      bash run_evaluate.sh \
        ${data_root_path}/${processed_data_dir}/TGTTEXT.${tgt_lang}/TGTTEXT.txt \
        ${output_dir}/raw_output.txt \
        ${output_dir}/sentences_output.${tgt_lang} \
        ${output_dir}/timestamps_output.${tgt_lang} \
        ${output_dir}/resegment_sentences_output.${tgt_lang} \
        ${main_metrics_paths} \
        ${task} \
        ${src_lang} \
        ${tgt_lang} \
        "none"

      if [[ ("$dataname" == IWSLT*) || ("$dataname" == mTEDx*) || ("$dataname" == tst-COMMON*) ]]; then
        # Also evaluate per audio for IWSLT
        for ((i=0;i<${nr_audio_files};i++));
        do
          main_metrics_paths_per_audio=${output_dir}/score_${i}.txt
          # Evaluate the main task
          bash run_evaluate.sh \
            ${data_root_path}/${processed_data_dir}/TGTTEXT.${tgt_lang}/TGTTEXT_${i}.txt \
            ${output_dir}/audio_${i}.txt \
            ${output_dir}/sentences_output_${i}.${tgt_lang} \
            ${output_dir}/timestamps_output_${i}.${tgt_lang} \
            ${output_dir}/resegment_sentences_output_${i}.${tgt_lang} \
            ${main_metrics_paths_per_audio} \
            ${task} \
            ${src_lang} \
            ${tgt_lang} \
            ${i}
          all_metrics_pahts+=(${main_metrics_paths_per_audio})
        done
      fi
    done
  fi

  # Evaluate the ASR component
  if [[ ($task == "cascadedST") || ($task == "ASR") ]]; then
    if [[ $task == "ASR" ]]; then
      ASR_task="ASR"
    else
      ASR_task="ASR_byProd_${task}"
    fi
    ASR_output_dir=${output_root_path}/${run_id}/${dataname}/${ASR_task}
    ASR_metrics_paths=${ASR_output_dir}/score.txt
    mkdir -p ${ASR_output_dir}
    if [[ $task != "ASR" ]]; then
      cp ${output_dir}/raw_output.txt ${ASR_output_dir}
      cp ${output_dir}/audio_*.txt ${ASR_output_dir}
    fi
    bash run_evaluate.sh \
      ${data_root_path}/${processed_data_dir}/SRCTEXT.${src_lang}/SRCTEXT.txt \
      ${ASR_output_dir}/raw_output.txt \
      ${ASR_output_dir}/sentences_output.${src_lang} \
      ${ASR_output_dir}/timestamps_output.${src_lang} \
      ${ASR_output_dir}/resegment_sentences_output.${src_lang} \
      ${ASR_metrics_paths} \
      ${ASR_task} \
      ${src_lang} \
      ${src_lang} \
      "none"
    all_metrics_pahts+=(${ASR_metrics_paths})
  fi

  if [[ ( ("$dataname" == IWSLT*) || ("$dataname" == mTEDx*) || ("$dataname" == tst-COMMON*) ) && ($task != "MT") ]]; then
    # Also evaluate per audio for IWSLT
    for ((i=0;i<${nr_audio_files};i++));
    do
      # When the task is ST, also evaluate the ASR component
      if [[ ($task == "cascadedST") ]]; then
        ASR_task="ASR_byProd_${task}"
        ASR_output_dir=${output_root_path}/${run_id}/${dataname}/${ASR_task}
        ASR_metrics_paths_per_audio=${ASR_output_dir}/score_${i}.txt
        bash run_evaluate.sh \
          ${data_root_path}/${processed_data_dir}/SRCTEXT.${src_lang}/SRCTEXT_${i}.txt \
          ${ASR_output_dir}/audio_${i}.txt \
          ${ASR_output_dir}/sentences_output_${i}.${src_lang} \
          ${ASR_output_dir}/timestamps_output_${i}.${src_lang} \
          ${ASR_output_dir}/resegment_sentences_output_${i}.${src_lang} \
          ${ASR_metrics_paths_per_audio} \
          ${ASR_task} \
          ${src_lang} \
          ${src_lang} \
          ${i}
        all_metrics_pahts+=(${ASR_metrics_paths_per_audio})
      fi
    done
  fi
} > >(tee "${output_dir}/logs.txt") 2>&1

# Log metrics
all_metrics_pahts=($(echo "${all_metrics_pahts[@]}" | tr ' ' '\n' | sort -u))
python log_runs.py \
  --function log_and_end_run \
  --metrics_paths ${all_metrics_pahts[@]} \
  --artifacts_path ${output_root_path}/${run_id} \
  --run_id ${run_id} \
  --local_mlflow_path "http://mlflow:5000"
