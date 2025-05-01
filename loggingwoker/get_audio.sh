
SESSION=$1 # e.g. /logs/2023/04/28/130525

name=(`docker compose ps | grep log`)
id=`docker ps -aqf "name=${name[0]}"`

docker compose exec log bash -c 'cd '"$SESSION"' && python -c "import json
import base64

wav = b'\''RIFF\xf0\xb2\x1e\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00LIST\x1a\x00\x00\x00INFOISFT\x0e\x00\x00\x00Lavf58.29.100\x00data\xaa\xb2\x1e\x00'\''

for line in open(\"user:0\",\"rb\"):
    data = json.loads(line.strip())
    if \"b64_enc_pcm_s16le\" in data:
        pcm_s16le = base64.b64decode(data[\"b64_enc_pcm_s16le\"])
        wav += pcm_s16le

with open(\"audio.wav\",\"wb\") as f:
    f.write(wav)"'

docker cp $id:$SESSION/audio.wav .
