#!/usr/bin/env python3
import argparse
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM, pipeline
# from transformers.pipelines.pt_utils import KeyDataset
# from datasets import load_dataset
from huggingface_hub import hf_hub_download
import fasttext

from code_map import iso_to_flores200

from flask import Flask, request


parser = argparse.ArgumentParser()
parser.add_argument("--model", type=str, default="facebook/nllb-200-1.3B", help="name of the model")
parser.add_argument("--device", type=str, default="cuda:0", help="Device to run on")
parser.add_argument("--flores200-langcodes", action='store_true',
                    help=("Use flores200 language codes for the model tokenizer, "
                          "e.g. `deu_Latn` instead of `de` for German"))
args = parser.parse_args()


lid = fasttext.load_model(hf_hub_download("facebook/fasttext-language-identification", "model.bin"))

tokenizer = AutoTokenizer.from_pretrained(args.model)
model = AutoModelForSeq2SeqLM.from_pretrained(args.model)
model.to(args.device)

translator = pipeline('translation',
                      model=model,
                      tokenizer=tokenizer)
translator.device = model.device

app = Flask(__name__)


@app.post('/predictions/mt-<source_lang>,<target_lang>')
def translate(source_lang, target_lang):
    sentences = request.form.get('text') or request.form.get('data')
    if isinstance(sentences, str):
        sentences = [sentences]

    if args.flores200_langcodes or 'nllb' in args.model:
        src_code = iso_to_flores200.get(source_lang, source_lang)
        tgt_code = iso_to_flores200.get(target_lang, target_lang)
    else:
        src_code = source_lang
        tgt_code = target_lang

    if src_code == 'unk':
        # assumnig that all batch items have the same source language for now
        (lid_src_lang,), _ = lid.predict(sentences[0])
        src_code = lid_src_lang.removeprefix('__label__')

    lenpen = 1.5
    num_beams = 5
    batch_size = 4
    no_repeat_ngram_size = 4
    translations = [
        tr['translation_text'] for tr in translator(sentences,
                                                    src_lang=src_code,
                                                    tgt_lang=tgt_code,
                                                    length_penalty=lenpen,
                                                    num_beams=num_beams,
                                                    batch_size=batch_size,
                                                    no_repeat_ngram_size=no_repeat_ngram_size)
    ]
    return '\n'.join(translations)


if __name__ == "__main__":
    app.run(host='0.0.0.0')
