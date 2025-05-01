
target_date="${1:-2024-05-14}" #"YYYY-MM-DD"
user=${2:-admin@example.com}

name=(`docker compose ps | grep log`)
id=`docker ps -aqf "name=${name[0]}"`

docker cp script.sh $id:/src/script.sh
docker cp script.py $id:/src/script.py
docker compose exec log bash -c "bash script.sh $target_date $user"

rm -rf data
docker cp $id:/src/data .

if ! ls data/*.wav 1> /dev/null 2>&1; then
  echo "No .wav files found in the data/ directory."
  exit 1
fi

for wav in data/*.wav
do
    if command -v ffmpeg &> /dev/null
    then
        echo "CONVERTING $wav"
        ffmpeg -f s16le -ar 16000 -ac 1 -i "$wav" "${wav%.wav}.mp3"
        rm "$wav"
    fi
done

