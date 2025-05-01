
import torch
import numpy as np
import math
from transformers import WhisperForConditionalGeneration, WhisperProcessor
from transformers.modeling_outputs import BaseModelOutput
from interface import FlaskInterface

host = "0.0.0.0"
port = 5008

def initialize_model():
    filename = "large-v3" # ["tiny.en","tiny","base.en","base","small.en","small","medium.en","medium","large-v1","large-v2","large"]
    
    model_path = "openai/whisper-{}".format(filename)
    processor = WhisperProcessor.from_pretrained(model_path)
    model = WhisperForConditionalGeneration.from_pretrained(model_path)

    if 50257 in model.generation_config.begin_suppress_tokens: # allow eos token to be first token (needed when prefix is already the whole transcript to prevent haluzination)
        model.generation_config.begin_suppress_tokens.remove(50257)
    
    print("ASR initialized")

    if torch.cuda.is_available():
        model = model.cuda()
    
    return {"model":model, "processor":processor, "max_batch_size":8}

def get_forced_decoder_ids(processor, prefix, task, prev_text_tokens, device):
    prompt_ids = None
    if len(prev_text_tokens) > 0:
        prompt_ids = torch.as_tensor(processor.get_prompt_ids(prev_text_tokens)).to(device) # <|startofprev|> ...

    forced_decoder_ids = processor.get_decoder_prompt_ids(language="en", task=task) # <|en|><|transcribe|><|notimestamps|>
    forced_decoder_ids[0] = (forced_decoder_ids[0][0],None) # Unset language
    if len(prefix) > 0:
        ids = processor.get_prompt_ids(prefix).tolist()[1:]
        for id in ids:
            forced_decoder_ids.append((len(forced_decoder_ids) + 1, id))

    #print(forced_decoder_ids)
    return forced_decoder_ids, prompt_ids

def infer_batch(model, processor, audio_wavs, prefix="", input_language="en", task="transcribe", audio_sample_rate=16000, prev_text_tokens=""):
    # get device based on the model parameters
    device = next(model.parameters()).device
        
    input_values = torch.cat([processor(item, sampling_rate=audio_sample_rate, return_tensors="pt").input_features for item in audio_wavs], dim=0).to(device)
    
    forced_decoder_ids, prompt_ids = get_forced_decoder_ids(processor, prefix, task, prev_text_tokens, device)
    model.generation_config.forced_decoder_ids = forced_decoder_ids

    predicted_ids = model.generate(
        input_values, 
        no_repeat_ngram_size=6,
        prompt_ids=prompt_ids
    )

    if prompt_ids is not None:
        predicted_ids = predicted_ids[:,len(prompt_ids):]

    #print(predicted_ids)
    #print(processor.batch_decode(predicted_ids))

    text_output_raw = processor.batch_decode(predicted_ids, skip_special_tokens=True)
    lids = [l[2:-2] for l in processor.batch_decode(predicted_ids[:,1])]
        
    if input_language != "None":
        input_languages = input_language.split("+")
        text_output_raw = [t if l in input_languages else prefix for t,l in zip(text_output_raw,lids)]

    return text_output_raw, lids

def pcm_s16le_to_tensor(pcm_s16le):
    audio_tensor = np.frombuffer(pcm_s16le, dtype=np.int16)
    audio_tensor = torch.from_numpy(audio_tensor)
    audio_tensor = audio_tensor.float() / math.pow(2, 15)
    return audio_tensor

def apply_model(inititalize_output, reqs):
    model, processor = inititalize_output["model"], inititalize_output["processor"]

    audio_tensors = [req.get("pcm_s16le") for req in reqs]
    prefixes = [req.get("prefix") for req in reqs]
    input_languages = [req.get("input_language") for req in reqs]
    output_languages = [req.get("output_language") for req in reqs]
    prev_text_tokens = [req.get("prev_text_tokens") for req in reqs]

    if any(a.shape[0] > 30 * 16000 for a in audio_tensors):
        print("WARNING: Audio is longer than 30 seconds, truncating!")

    batch_runnable = False

    if len(set(prefixes)) == 1 and len(set(input_languages)) == 1 and len(set(output_languages)) == 1 and len(set(prev_text_tokens)) == 1:
        batch_runnable = True

    if batch_runnable:
        if input_languages[0] == output_languages[0]:
            task = "transcribe"
        elif output_languages[0] == 'en':
            task = "translate"
        else:
            for req in reqs:
                result = {"hypo": "", "status":400, "message": 'Wrong option. Perform X->X "transcribe" or X->English "translate". Found {} -> {}'.format(input_languages[0], output_languages[0])}
                req.publish(result)
            return
        hypos, lids = infer_batch(model, processor, audio_wavs=audio_tensors, input_language=input_languages[0], task=task, prefix=prefixes[0], prev_text_tokens=prev_text_tokens[0])

        for req, hypo, lid in zip(reqs, hypos, lids):
            result = {"hypo": hypo.strip(), "lid":lid}
            req.publish(result)
    else:
        for req, audio_tensor, prefix, input_language, output_language, prev_text_tokens_ \
                in zip(reqs, audio_tensors, prefixes, input_languages, output_languages, prev_text_tokens):
            if input_language == output_language:
                task = "transcribe"
            elif output_language == 'en':
                task = "translate"
            else:
                result = {"hypo": "", "status":400, "message": 'Wrong option. Perform X->X "transcribe" or X->English "translate". Found {} -> {}'.format(input_language, output_language)}
                req.publish(result)
                continue
            hypos, lids = infer_batch(model, processor, audio_wavs=[audio_tensor], input_language=input_language, task=task, prefix=prefix, prev_text_tokens=prev_text_tokens_)
            result = {"hypo": hypos[0].strip(), "lid":lids[0]}
            req.publish(result)

inputs = [{"name":"pcm_s16le","type":bytes},
          {"name":"input_language","type":str},
          {"name":"output_language","type":str},
          {"name":"prefix","type":str,"default":""},
          {"name":"prev_text_tokens","type":str,"default":""},
          {"name":"priority","type":int,"default":0},
         ]

def map_request_data(request_data):
    request_data["pcm_s16le"] = pcm_s16le_to_tensor(request_data["pcm_s16le"])
    return request_data

app = FlaskInterface(inputs,initialize_model,apply_model,map_request_data=map_request_data)

# called during automatic evaluation of the pipeline to store worker information
@app.route("/asr/version", methods=["POST"])
def version():
    # return dict or string (as first argument)
    return "Whisper large v3", 200

@app.route("/asr/available_languages", methods=["GET","POST"])
def languages():
    langs = [x[2:-2] for x in processor.tokenizer.additional_special_tokens if 6<=len(x)<=7]
    return langs

if __name__ == "__main__":
    app.run(port=5008)

    # example call:
    # import requests
    # wav = open("/project/data_asr/common_voice_multilingual/cv-corpus-5.1-2020-06-22/fr/wav/common_voice_fr_17299384.wav","rb").read()[78:]
    # requests.post("http://192.168.0.72:5008/inference", files={"pcm_s16le":wav, "input_language":"fr", "output_language":"fr"})

