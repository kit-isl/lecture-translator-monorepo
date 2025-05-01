
import sys
import json
import base64
import os

infile = sys.argv[1]+"user:0"

def get_empty_wav():
    wav = b'' #b'RIFF\xf0\xb2\x1e\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00LIST\x1a\x00\x00\x00INFOISFT\x0e\x00\x00\x00Lavf58.29.100\x00data\xaa\xb2\x1e\x00'
    return [wav]

def write_wav(name, wav, memory_words):
    wav = b''.join(wav)

    with open(name,"wb") as f:
        f.write(wav)

    words = set()
    for d in memory_words:
        memory_words_ = json.loads(d["memory_words"])
        if not memory_words_:
            continue
        for word in memory_words_:
            words.add(word)
    with open(name[:-len("wav")]+"memory", "w") as f:
        for word in sorted(list(words)):
            f.write(word+"\n")

if os.path.isfile(infile):
    wav = start = None
    memory_words = []
    outfile = sys.argv[2].replace(" ","_")

    for line in open(infile, "rb"):
        data = json.loads(line.strip())
        if "b64_enc_pcm_s16le" in data:
            if wav is None and "start" in data:
                start = data["start"]
                wav = get_empty_wav()

            if data["b64_enc_pcm_s16le"] != "UEFVU0U=":
                pcm_s16le = base64.b64decode(data["b64_enc_pcm_s16le"])
                wav.append(pcm_s16le)
            elif start and wav:
                write_wav(f"{outfile}_{start:.2f}.wav", wav, memory_words)
                memory_words = memory_words[-1:]

                wav = None
        elif "memory_words" in data:
            memory_words.append(data)

    if start and wav:
        write_wav(f"{outfile}_{start:.2f}.wav", wav, memory_words)

