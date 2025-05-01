#!/usr/bin/env python3
import argparse
import json
import time
import re
import jsons
import nltk
from nltk import sent_tokenize
import redis
import requests
from transformers import AutoTokenizer
import fasttext
import threading

from qbmediator.Connection import get_best_connector
from qbmediator.Database import get_best_database
from qbmediator.Interface import MediatorInterface, ActiveSessionStorage
from qbmediator.Session import Session

nltk.download('punkt')

from qbmediator.Text import get_punctuation

punctuation = get_punctuation()

property_to_default = {
    "display_language": "en",
    "method": "streaming_simple",
    "model_parameters": {
        "summarizer": {
            "endpoint": "http://192.168.0.73:8054",
        },
        "online_model": {
            "endpoint": "http://192.168.0.68:3001",
            "look_ahead": 5, # don't change unless you know what you do
            "prefix_titles": True,
            "threshold": 0.4,
            "paragraph_every_n_sentences": 5,
        },
        "offline_model": {
            "endpoint": "http://192.168.0.68:3000",
            "titles": True,
            "prefix_titles": True,
            "threshold": 0.4,
            "paragraph_every_n_sentences": 5,
        },
        "streaming_simple": {
            "new_paragraph_each_speechsegment": True,
            "min_dist_last_speech_segment_ends": 30,
        },
        "streaming_tts": {
            "send_min_words": 10,
            "send_max_words": 20,
            "max_wait": 2.5,
        },
        "speaker_diarization": {
        }
    },
    "speaker_diarization_endpoint": "http://localhost",
    "new_language_new_paragraph": "True",
    "mode": "SendUnstable",
}

class TextGeneratorClient:
    def __init__(self, host_url):
        self.host_url = host_url.rstrip("/") + "/generate"
        self.host_url_stream = host_url.rstrip("/") + "/generate_stream"

    def generate_text(self, prompt, max_new_tokens=100, do_sample=True, temperature=0.8):
        payload = {
            'inputs': prompt,
            'parameters': {
                'max_new_tokens': max_new_tokens,
                'do_sample': do_sample,
                'temperature': temperature,
            }
        }

        headers = {
            'Content-Type': 'application/json'
        }

        response = requests.post(self.host_url, data=json.dumps(payload), headers=headers).json()
        text = response["generated_text"]
        return text

    def generate_text_stream(self, prompt, max_new_tokens=100, do_sample=True, temperature=0.8, stop=[], best_of=1):
        import requests
        import json

        payload = {
            'inputs': prompt,
            'parameters': {
                'max_new_tokens': max_new_tokens,
                'do_sample': do_sample,
                'temperature': temperature,
                'stop': stop,
                'stop': stop,
                'best_of': best_of,
            }
        }

        headers = {
            'Content-Type': 'application/json',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive'
        }

        response = requests.post(self.host_url_stream, data=json.dumps(payload), headers=headers, stream=True)

        for line in response.iter_lines():
            if line:
                json_data = line.decode('utf-8')
                if json_data.startswith('data:'):
                    json_data = json_data[5:]
                    token_data = json.loads(json_data)
                    token = token_data['token']['text']
                    if not token_data['token']['special']:
                        yield token

class OfflineTextSegmenterClient:
    def __init__(self, host_url):
        self.host_url = host_url.rstrip("/") + "/segment"

    def segment(self, text, captions=None, generate_titles=False, prefix_titles=True, threshold=0.5):
        payload = {
            'text': text.lstrip(),
            'captions': captions,
            'generate_titles': generate_titles,
            'prefix_titles': prefix_titles,
            'threshold': threshold,
        }

        headers = {
            'Content-Type': 'application/json'
        }

        response = requests.post(self.host_url, data=json.dumps(payload), headers=headers).json()
        segments =  response["annotated_segments"] if "annotated_segments" in response else response["segments"]
        return {'segments':segments, 'titles': response["titles"]}

class OnlineTextSegmenterClient:
    def __init__(self, host_url):
        self.host_url = host_url.rstrip("/") + "/segment"

    def segment(self, text, captions=True, generate_titles=True, prefix_titles=True, previous_titles=None, threshold=0.5):
        payload = {
            'text': text.lstrip(),
            'captions': captions,
            'generate_titles': generate_titles,
            'prefix_titles': prefix_titles,
            'previous_titles': previous_titles,
            'threshold': threshold,
        }

        headers = {
            'Content-Type': 'application/json'
        }

        response = requests.post(self.host_url, data=json.dumps(payload), headers=headers).json()
        return {'boundaries': response["boundaries"], 'titles': response["titles"]}

class Truncater:
    def __init__(self, tokenizer, *, max_length):
        self.max_length = max_length
        self.tokenizer = tokenizer

    def __call__(self, text):
        return self.truncate(text)

    def truncate(self, text):
        input_ids = self.tokenizer.encode(text, add_special_tokens=False, truncation=True, max_length=self.max_length)
        return self.tokenizer.decode(input_ids)

class SummarizerInterface(MediatorInterface):
    PROMPT_TEMPLATE = """<s>[INST] <<SYS>>
You are an assistant who replies with a summary to every message.
<</SYS>>
{prompt_text}\n\n{input_text} [/INST]"""

    prompt_text = "Summarize the following in three bullet points:"
    prompt_text_tldr = "Give a short summary (a single sentence):"

    #tokenizer = AutoTokenizer.from_pretrained("togethercomputer/LLaMA-2-7B-32K", use_fast=False)
    #truncater = Truncater(tokenizer, max_length=950)

    IGNORED_SEQUENCES = set(["<br /><br />"])

    def __init__(self, name, session, tag, endpoint):
        super().__init__(name, session, tag, mode="SendUnstable")
        self.client = TextGeneratorClient(endpoint)
        self.current_headline = ""
        self.current_segment = ""
        self.current_segment_n = 0
        self.current_start = 0
        self.current_end = 0

    @staticmethod
    def markdown_to_html(markdown_list):
        html_list = "<ul>\n"
        items = markdown_list.split("\n")
        items = [item.strip().replace("* ", "") for item in items if item.strip() != ""]
        for item in items:
            html_list += f"<li>{item}</li>\n"
        html_list += "</ul>"
        return html_list

    def generate_tldr(self, input_, prefix=""):
        input_ = self.truncater(input_)
        all_generated_text = prefix
        for generated_text in self.client.generate_text_stream(self.PROMPT_TEMPLATE.format(prompt_text=self.prompt_text_tldr, input_text=input_), max_new_tokens=250, do_sample=True, temperature=0.8):
            all_generated_text += generated_text
            if all_generated_text.count("\n") > 1:
                break
            self.send_text(0, 0, self.current_headline + all_generated_text, stable=False)
        return all_generated_text.strip()

    def generate_bullet_points(self, input_, prefix=""):
        input_ = self.truncater(input_)
        all_generated_text = prefix
        has_generated = False
        tries = 6
        while not has_generated:
            for i, generated_text in enumerate(self.client.generate_text_stream(self.PROMPT_TEMPLATE.format(prompt_text=self.prompt_text, input_text=input_), max_new_tokens=250, do_sample=True, temperature=0.8, stop=["\n###"])):
                if i == 0 and generated_text not in [" *", " -"]:
                    tries -= 1
                    print("retrying")
                    continue
                elif i > 0 and generated_text in [" *", " -"] and all_generated_text[-1] != "\n":
                    all_generated_text += "\n"
                all_generated_text += generated_text
                if all_generated_text.count("\n") >= 3:
                    has_generated = True
                    break
                self.send_text(0, 0, self.current_headline + self.markdown_to_html(all_generated_text), stable=False)
                if tries <= 0:
                    return self.generate_tldr(input_, prefix)
            if i > 0:
                has_generated = True
        return all_generated_text.strip()

    def handle_data(self, data):
        stable = not data["unstable"]
        is_new_segment = data["speech_segment_begins"]
        text = data["seq"]

        if text in self.IGNORED_SEQUENCES:
            return

        if is_new_segment and self.current_segment != "":
            self.handle_end_of_segment()

        if stable:
            self.current_end = float(data["end"])
            if is_new_segment:
                self.current_start = float(data["start"])
                headline, text = text.split("</h2>")
                self.current_headline = headline + "</h2>" 

            self.current_segment += text
            self.current_segment_n += 1

    def handle_end_of_segment(self):
        print("Handle segment")
        if self.current_segment_n <= 4:
            summary = self.generate_tldr(self.current_segment)
        else:
            summary = self.markdown_to_html(self.generate_bullet_points(self.current_segment))
        self.send_text(self.current_start, self.current_end, self.current_headline + summary, stable=True)
        self.current_segment = ""
        self.current_segment_n = 0
        print("Done handling segment")

class OnlineSummarizer(SummarizerInterface):
    def process(self):
        print("---- Processing ----")
        data = self.read_text()
        print(data)
        if data is None:
            return
        self.handle_data(data)

    def stop_working(self):
        print("---\n\n Summarizing last segment. \n\n---")
        self.running = False
        self.handle_end_of_segment()
        self.send_end()

class OfflineSummarizer(SummarizerInterface):
    def process(self):
        pass
    
    def stop_working(self):
        while True:
            data = self.read_text()
            if data is None:
                break
            self.handle_data(data)
        self.handle_end_of_segment()
        self.send_end()

class OfflineTextSegmenter(MediatorInterface):
    def __init__(self, name, session, tag, endpoint, titles=False, prefix_titles=True, threshold=0.5, paragraph_every_n_sentences=5):
        super().__init__(name, session, tag, mode="SendStable")
        self.titles = titles
        self.prefix_titles = prefix_titles
        self.threshold = threshold
        self.paragraph_every_n_sentences = paragraph_every_n_sentences
        self.client = OfflineTextSegmenterClient(endpoint)

    def process(self):
        pass
    
    def stop_working(self):
        print("--- \n\n Start Offline Segmentation \n\n ---")
        self.running = False

        # Collect all text
        captions = []
        document = ""
        while True:
            data = self.read_text()
            if data is None:
                break

            text = data["seq"]
            start = float(data["start"])
            end = float(data["end"])
            stable = not data["unstable"]

            if stable:
                document += text
                captions.append({'text': text, 'start': start, 'end': end})

        print("Document -- ", document)

        # Segment
        result = self.client.segment(document, captions=captions, generate_titles=self.titles, prefix_titles=self.prefix_titles, threshold=self.threshold)
        annotated_segments = result["segments"]
        titles = result["titles"]
        for i, segment in enumerate(annotated_segments):
            if self.titles:
                title = titles[i]
            for i, sentence in enumerate(segment):
                is_first = i == 0
                is_last = i == len(segment)-1
                start = sentence["start"]
                end = sentence["end"]
                text = sentence["text"]
                segment_begins = False
                if is_first:
                    segment_begins = True
                    if self.titles:
                        text = "<h2>" + title + "</h2>" + text
                self.send_text(start, end, text, stable=True, segment_begins=segment_begins)
                if self.paragraph_every_n_sentences is not None and (i+1) % self.paragraph_every_n_sentences == 0:
                    self.send_text(end, end, "<br /><br />", stable=True, segment_begins=False)
        self.send_end()

class OnlineTextSegmenter(MediatorInterface):
    def __init__(self, name, session, tag, endpoint, look_ahead=5, prefix_titles=True, threshold=0.5, paragraph_every_n_sentences=5):
        super().__init__(name, session, tag, mode="SendUnstable")
        self.look_ahead = look_ahead
        self.prefix_titles = prefix_titles
        self.threshold = threshold
        self.paragraph_every_n_sentences = paragraph_every_n_sentences
        self.current_sentence_idx = 0 # keep track for paragraphing

        self.client = OnlineTextSegmenterClient(endpoint)

        self.sentences = []
        self.current_text = {"start": 0, "end": 0, "text": ""}
        self.num_segments = 0
        self.segmented_senteneces = []
        self.titles = []
    
    def _get_texts(self, list_):
        return [s["text"] for s in list_]

    def _headline(self, text):
        return "<h2>" + text + "</h2>"

    @property
    def _all_text(self):
        return " ".join(self._get_texts(self.segmented_senteneces) + self._get_texts(self.sentences))

    @property
    def _all_sentences(self):
        return self.segmented_senteneces + self.sentences

    @property
    def _prefix_titles(self):
        return {"prefix_titles": self.prefix_titles, "previous_titles": self.titles if self.prefix_titles else None}

    def stop_working(self):
        """
        Process the remaining sentences that have not been segmented and sent yet.
        """
        print("--- \n\n Online segmentation -- segment remaining sentences \n\n ---")
        self.running = False

        result = self.client.segment(self._all_text, self._all_sentences, **self._prefix_titles)
        index = len(self.segmented_senteneces)
        for i, boundary in enumerate(result["boundaries"][index:]):
            self.handle_segmentation_result(index+i, result, boundary)
        self.send_end()

    def process(self):
        data = self.read_text()
        if data is None:
            return

        start = float(data["start"])
        end = float(data["end"])
        text = data["seq"]
        stable = not data["unstable"]

        if stable:
            self.current_text["end"] = end
            self.current_text["text"] += text
            self.current_text["text"] = self.current_text["text"].replace("  ", " ")

            sentence_candidates = sent_tokenize(self.current_text["text"])
            if len(sentence_candidates) > 1:
                duration = self.current_text["end"] - self.current_text["start"]
                sentence_end = self.current_text["start"] + duration * len(sentence_candidates[0]) / len(self.current_text["text"])

                self.sentences.append({"text": sentence_candidates[0], "start": self.current_text["start"], "end": sentence_end})

                self.current_text["text"] = " ".join(sentence_candidates[1:])
                self.current_text["text"] += " " if self.current_text["text"][-1] != " " else ""
                self.current_text["start"] = sentence_end

                self.send_unstable_text()
                sentence_candidates.pop(0)

                if len(self.sentences) >= self.look_ahead:
                    self.current_sentence_idx += 1
                    index = len(self.segmented_senteneces)
                    result = self.client.segment(self._all_text, self._all_sentences, **self._prefix_titles)
                    boundary = result["boundaries"][index]
                    self.handle_segmentation_result(index, result, boundary)
                    if boundary:
                        self.current_sentence_idx = 0
                    elif (self.current_sentence_idx+1) % self.paragraph_every_n_sentences == 0:
                        self.send_text(end, end, "<br /><br />", stable=True, segment_begins=False) 

            self.send_unstable_text()

    def handle_segmentation_result(self, index, result, boundary):
        start = self.sentences[0]["start"]
        end = self.sentences[0]["end"]
        raw_text = self.sentences[0]["text"]
        headline = None
        if boundary:
            segment_begins = True
            segment_id = sum(result["boundaries"][:index+1])
            headline = result["titles"][segment_id]
            headline_html = self._headline(headline)
            text = headline_html + raw_text + " "
            self.num_segments += 1
        elif self.num_segments == 0:
            segment_begins = True
            headline = result["titles"][0]
            headline_html = self._headline(headline)
            text = headline_html + raw_text + " "
            self.num_segments += 1
        else:
            segment_begins = False
            text = raw_text + " "
        self.send_text(start, end, text, stable=True, segment_begins=segment_begins)

        self.segmented_senteneces.append(self.sentences[0])
        self.sentences.pop(0)

        if headline is not None:
            self.titles.append(headline)
                    
    def send_unstable_text(self):
        unstable_text = " ".join(self._get_texts(self.sentences)) + " " + self.current_text["text"]
        if len(self.sentences) > 0:
            #self.send_text(self.sentences[-1]["start"], self.sentences[-1]["end"], unstable_text, False)
            self.send_text(self.sentences[-1]["start"], self.current_text["end"], unstable_text, False)
            

class WordBuffer:
    def __init__(self, buffer_size=15):
        self.buffer = []
        self.buffer_size = buffer_size
        self.lock = threading.Lock()
        self.flush_timer = None
        self.flush_delay = 3
        self.last_word_time = None

    def add_text(self, text):
        # with self.lock:
        words = re.findall(r'\S+', text)  # Split text into words
        self.buffer.extend(words)
            # self.last_word_time = time.time()
        self._reset_flush_timer()
        result = []
        
        while len(self.buffer) >= self.buffer_size:
            chunk = ' '.join(self.buffer[:self.buffer_size])
            result.append(chunk)
            self.buffer = self.buffer[self.buffer_size:]
        
        return result
    
    def _reset_flush_timer(self):
        if self.flush_timer:
            self.flush_timer.cancel()
        self.flush_timer = threading.Timer(self.flush_delay, self._delayed_flush)
        self.flush_timer.start()

    def _delayed_flush(self):
        print('buffer: ', self.buffer)
        with self.lock:
            if self.buffer:
                result = ' '.join(self.buffer)
                self.buffer = []
                return [result]
        return []

class PrefixSentenceAligner:
    def __init__(self, n=3):
        self.n = n

    def preprocess(self, text):
        return ''.join(c.lower() for c in text if c.isalnum() or c.isspace())

    def generate_ngrams(self, text):
        return [text[i:i+self.n] for i in range(len(text) - self.n + 1)]

    def prefix_similarity(self, s1, s2):
        min_len = min(len(s1), len(s2))
        for i in range(min_len):
            if s1[i] != s2[i]:
                return i / min_len, s1[:i], s2[i:]
        return 1.0, s1[:min_len], s2[min_len:]

    def progressive_similarity(self, s1, s2):
        processed_s1 = self.preprocess(s1)
        processed_s2 = self.preprocess(s2)

        # First, check prefix similarity
        prefix_sim, common_prefix, non_common_part = self.prefix_similarity(processed_s1, processed_s2)
        
        if prefix_sim == 1.0:
            return 1.0, common_prefix, non_common_part  # Perfect match
        
        # If prefix similarity is high, proceed with n-gram comparison
        if prefix_sim > 0.5:
            ngrams1 = set(self.generate_ngrams(processed_s1))
            ngrams2 = set(self.generate_ngrams(processed_s2))
            
            intersection = len(ngrams1.intersection(ngrams2))
            union = len(ngrams1.union(ngrams2))
            
            ngram_sim = intersection / union if union > 0 else 0
            
            # Combine prefix and n-gram similarity
            return (prefix_sim + ngram_sim) / 2, common_prefix, non_common_part
        
        return prefix_sim, common_prefix, non_common_part

    def print_alignments(self, sentences1, sentences2, alignments):
        for i, j, score, common, non_common in alignments:
            print(f"Sentence 1 [{i}]: {sentences1[i]}")
            print(f"Sentence 2 [{j}]: {sentences2[j]}")
            print(f"Similarity Score: {score:.4f}")
            print(f"Common Part: '{common}'")
            print(f"Non-Common Part (Sentence 2): '{non_common}'")
            print()



class TTSTextSegmenter(MediatorInterface):
    def __init__(self, name, session, tag, send_min_words=None, send_max_words=None, max_wait=None, mode=None, *args, **kwargs):
        super().__init__(name, session, tag, mode="SendStable")

        self.send_min_words = send_min_words
        self.send_max_words = send_max_words
        self.max_wait = max_wait

        self.start = None
        self.next_callback = None
        self.buffer = ""
        # self.wordbuffer = WordBuffer(buffer_size=15)
        self.mode = "fix"
        self.latest_history = ''
        self.aligner = PrefixSentenceAligner(n=3)
        # for word-buffer:
        # self.buffer = []
        self.buffer_size = 10
        self.lock = threading.Lock()
        self.flush_timer = None
        self.flush_delay = 3
        self.last_word_time = None
        
    def add_text(self, text):
        # with self.lock:
        words = re.findall(r'\S+', text)  # Split text into words
        self.buffer.extend(words)
        self._reset_flush_timer()
        result = []
        
        while len(self.buffer) >= self.buffer_size:
            chunk = ' '.join(self.buffer[:self.buffer_size])
            result.append(chunk)
            self.buffer = self.buffer[self.buffer_size:]
        
        return result
    
    def _reset_flush_timer(self):
        if self.flush_timer:
            self.flush_timer.cancel()
        self.flush_timer = threading.Timer(self.flush_delay, self._delayed_flush)
        self.flush_timer.start()

    def _delayed_flush(self):
        print('buffer: ', self.buffer)
        with self.lock:
            if self.buffer:
                result = ' '.join(self.buffer)
                self.buffer = []
                return [result]
        return []

    def stop_working(self):
        print("Stopping working.")
        self.running = False
        self.send_end()
        
    def repeat_check(self, new_data):
        prefix_sim, common_prefix, non_common_part = self.aligner.progressive_similarity(self.latest_history, new_data)
        print('self.latest_history:', self.latest_history)
        print('new_data:', new_data)
        print('prefix_sim:', prefix_sim)
        if prefix_sim >0.4:
            return non_common_part
        return new_data

    def process(self):
        data = self.read_text()
        print(data)
            
        do_callback = data is None and self.next_callback is not None and time.time() > self.next_callback and self.start is not None and self.buffer != ""
        
        if do_callback:
            self.send_raw_text(self.start, self.end, self.buffer, True, additional_dict={"speakerName": data["speakerName"]} if "speakerName" in data else None)
            self.start = self.end
            self.buffer = " "
            print("Sent because of max_wait")
        elif data is None:
            return

        stable = not data["unstable"]

        # if True:
        if stable:
            start = float(data["start"])
            end = float(data["end"])
            if self.start is None:
                self.start = start
            self.end = end

            text = data["seq"]
            
            self.buffer += text

            speech_segment_ends = data["speech_segment_ends"] if "speech_segment_ends" in data else False
            if speech_segment_ends:
                # self.buffer = self.repeat_check(self.buffer)
                if self.buffer!="":
                    self.send_raw_text(self.start, self.end, self.buffer, True, additional_dict={"speakerName": data["speakerName"]} if "speakerName" in data else None)
                    self.latest_history = self.buffer
                    self.buffer = " "
                    self.start = None
                    print("Sent because of speech_segment_ends")
                    self.next_callback = None
                    return
            
            b = self.buffer.split()
            index = max([i for i,w in enumerate(b) if any(p in w for p in punctuation)], default=-1)
            if index != -1:
                send_text = " "+(" ".join(b[:index+1]))
                self.buffer = " "+(" ".join(b[index+1:]))

                end = (self.end-self.start)*index/len(b)+self.start
                # send_text = self.repeat_check(send_text)
                if send_text!="":
                    self.send_raw_text(self.start, end, send_text, True, additional_dict={"speakerName": data["speakerName"]} if "speakerName" in data else None)
                    self.latest_history = send_text
                    self.start = end
                    print("Sent because of punctuation")

                if len(text) != 0:
                    self.next_callback = time.time() + self.max_wait
                return

            split_before = ["and","or","but","und","oder","aber"]
            index = max([i for i,w in enumerate(b[self.send_min_words-1:],self.send_min_words-1) if w in split_before], default=-1)
            if index != -1:
                send_text = " "+(" ".join(b[:index]))
                self.buffer = " "+(" ".join(b[index:]))

                end = (self.end-self.start)*(index+1)/len(b)+self.start
                # send_text = self.repeat_check(send_text)
                if send_text!="":
                    self.send_raw_text(self.start, end, send_text, True, additional_dict={"speakerName": data["speakerName"]} if "speakerName" in data else None)
                    self.start = end
                    print("Sent because of word")
                    self.latest_history = send_text

                if len(text) != 0:
                    self.next_callback = time.time() + self.max_wait
                return

            if len(b) > 20:
            # self.send_max_words:
                # self.buffer = self.repeat_check(self.buffer)
                if self.buffer!="":
                    self.send_raw_text(self.start, self.end, self.buffer, True, additional_dict={"speakerName": data["speakerName"]} if "speakerName" in data else None)
                    self.start = self.end
                    self.latest_history = self.buffer
                    self.buffer = " "
                    print("Sent because of max length")
                self.next_callback = time.time() + self.max_wait
                return
        # if stable:
        #     start = float(data["start"])
        #     if self.start is None:
        #         self.start = start
        #     self.end = float(data["end"])
        #     self.send_raw_text(self.start, self.end, data["seq"], True, additional_dict={"speakerName": data["speakerName"]} if "speakerName" in data else None) 
            # start = float(data["start"])
            # end = float(data["end"])
            # if self.start is None:
            #     self.start = start
            # self.end = end
            # text = data["seq"]
            # speech_segment_ends = data["speech_segment_ends"] if "speech_segment_ends" in data else False
            # chunks = self.add_text(text, )
            # if len(chunks) > 0:
            #     for chunk in chunks:
            #         print('chunk: ', chunk)
            #         self.send_raw_text(self.start, self.end, chunk, True, additional_dict={"speakerName": data["speakerName"]} if "speakerName" in data else None)
        #     elif (time.time() - self.time) > 0.30:
        # #         # self.buffer = self.repeat_check(self.buffer)
        #         chunks = self.wordbuffer.flush()
        #         if len(chunks) > 0:
        #             for chunk in chunks:
        #                 print('chunk: ', chunk)
        #                 self.send_raw_text(self.start, self.end, chunk, True, additional_dict={"speakerName": data["speakerName"]} if "speakerName" in data else None)
        #                 print("Sent because of speech_segment_ends")
            
                
                
class SimpleStreamingTextSegmenter(MediatorInterface):
    def __init__(self, name, session, tag, new_paragraph_each_speechsegment=True, min_dist_last_speech_segment_ends=None, new_language_new_paragraph=False, mode="SendUnstable", *args, **kwargs):
        super().__init__(name, session, tag, mode=mode)

        self.num_points = 0
        self.last_speech_segment_ends = 0

        self.new_paragraph_each_speechsegment = new_paragraph_each_speechsegment
        self.min_dist_last_speech_segment_ends = min_dist_last_speech_segment_ends
        self.new_language_new_paragraph = new_language_new_paragraph

        if self.new_language_new_paragraph:
            self.lid_model = fasttext.load_model("lid.176.ftz")
            self.buffer = ["","",""] # stable published, stable unpublished, unstable unpublished
            self.last_stable_end = None
            self.last_lang = None

    def stop_working(self):
        print("Stopping working.")
        self.running = False
        self.send_end()

    def process(self):
        data = self.read_text()
        if data is None:
            return

        start = float(data["start"])
        end = float(data["end"])
        text = data["seq"]
        stable = not data["unstable"]
        speech_segment_ends = data["speech_segment_ends"] if "speech_segment_ends" in data else False

        if not self.new_language_new_paragraph:
            if stable:
                num_points_now = text.count(". ")
                if len(text)>0 and text[-1]==".":
                    num_points_now += 1

                split_after_n_points = 5
                if self.new_paragraph_each_speechsegment and (speech_segment_ends and end > self.last_speech_segment_ends + self.min_dist_last_speech_segment_ends):
                    text += "<br><br>"
                    self.num_points = 0
                    self.last_speech_segment_ends = end
                elif self.num_points + num_points_now >= split_after_n_points:
                    text = text.split(". ")
                    index = split_after_n_points-self.num_points
                    if index < 1 or index >= len(text):
                        index = len(text)
                    text2 = ". ".join(text[:index])
                    if len(text2) > 0 and text2[-1] != ".":
                        text2 += "."
                    text2 += "<br><br> "
                    text2 += ". ".join(text[index:])
                    text = text2
                    self.num_points = 0
                    self.last_speech_segment_ends = end
                else:
                    self.num_points += num_points_now

            self.send_text(start, end, text, stable)
        else:
            if len(text)>0 and text[-1]==".":
                text += " "

            min_words_predict = 5
            split_after_n_points = 5

            if (not ". " in text and self.buffer[1] == "") or not stable:
                if stable:
                    self.buffer[0] += text
                    self.last_stable_end = end
                    print(1,start,end,"|"+text+"|")
                    if len(text)>0 and text[0] != " ":
                        text = " "+text
                    self.send_text(start, end, text, True)
                else:
                    self.buffer[2] = text
                    self.send_text(self.last_stable_end if self.last_stable_end is not None else start, end, self.buffer[1]+text, False)
            else:
                utts = text.split(". ")
                sum_chars = 0
                for utt in utts[:-1]:
                    utt = " "+self.buffer[1]+utt+". "
                    self.buffer[0] += utt
                    self.buffer[1] = ""
                    lang, prob = self.lid_model.predict(self.buffer[0].strip())
                    print(1,lang,self.buffer[0].strip())

                    self.num_points += self.buffer[0].count(". ")
                    self.buffer[0] = ""

                    if self.num_points >= split_after_n_points:
                        utt += "<br><br> "
                        self.num_points = 0
                        lang = None
                    elif self.last_lang is not None and lang != self.last_lang:
                        utt = "<br><br> "+utt
                        self.num_points = 0

                    self.last_lang = lang

                    start_ = sum_chars*(end-start)/len(text)
                    sum_chars += len(utt)
                    end_ = sum_chars*(end-start)/len(text)
                    print(2,start+start_,start+end_,"|"+utt+"|")
                    self.send_text(start+start_, start+end_, utt, True)

                    self.last_stable_end = start+end_

                utt = utts[-1]
                if self.buffer[1] == "":
                    self.buffer[1] = " "
                self.buffer[1] += utt
                self.buffer[2] = ""

                start_ = sum_chars*(end-start)/len(text)
                sum_chars += len(utt)
                end_ = sum_chars*(end-start)/len(text)

                if len(self.buffer[1].split()) >= min_words_predict:
                    lang, prob = self.lid_model.predict(self.buffer[1].strip())
                    print(2,lang,self.buffer[1].strip())

                    utt_send = self.buffer[1]
                    self.buffer[0] += utt_send
                    self.buffer[1] = ""
                    if self.last_lang is not None and lang != self.last_lang:
                        utt_send = "<br><br>"+utt_send
                        self.num_points = 0

                    self.last_lang = lang

                    print(3,self.last_stable_end,start+end_,"|"+utt_send+"|")
                    self.send_text(self.last_stable_end, start+end_, utt_send, True)

                    self.last_stable_end = start+end_
                elif len(self.buffer[1].strip()) > 0:
                    self.send_text(self.last_stable_end, start+end_, self.buffer[1], False)

class SpeakerDiarization(MediatorInterface):
    def __init__(self, *args, endpoint=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.user_end = False
        self.endpoint = endpoint

    def stop_working(self):
        if not self.user_end:
            self.user_end = True
            return False

        print("Running speaker diariazation.")
        self.running = False

        all_text = []
        while True:
            text = self.read_text()
            if text is None:
                break
            all_text.append(text)

        audio = self.read_audio()
        print(self.endpoint)
        # Do stuff
        for text in all_text:
            self.send_text(text["start"],text["end"],f'Christian: Test {text["seq"]}<br><br>',stable=True)

        self.send_end()

    def process(self):
        pass
    
    

class TextSegmenterActiveSessionStorage(ActiveSessionStorage):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, use_callbacks=True, **kwargs)
        self.start_time = 0.0

    def on_start(self, key, data):
        self.start_time = time.time()  # Start the timer
        name, session, tag = key

        db.setContext(name,session,tag)

        parameters = db.getPropertyValues("model_parameters")
        # method = db.getPropertyValues("method")
        method = "streaming_tts"
         
        if "summarizer" in tag:
            if method == "offline_model":
                text_segmenter = OfflineSummarizer(*key, **parameters["summarizer"])
            elif method == "online_model":
                text_segmenter = OnlineSummarizer(*key, **parameters["summarizer"])
            else:
                return
        else:
            language = db.getPropertyValues("display_language")
            if method in ("offline_model","online_model") and language != "en":
                db.setPropertyValue("method","streaming_simple")
                method = "streaming_simple"

            if method == "offline_model":
                text_segmenter = OfflineTextSegmenter(*key, **parameters[method])
            elif method == "online_model":
                text_segmenter = OnlineTextSegmenter(*key, **parameters[method])
            elif method == "streaming_simple":
                new_language_new_paragraph = db.getPropertyValues("new_language_new_paragraph") == "True"
                mode = db.getPropertyValues("mode")
                text_segmenter = SimpleStreamingTextSegmenter(*key, new_language_new_paragraph=new_language_new_paragraph, mode=mode, **parameters[method])
            elif method == "streaming_tts":
                mode = db.getPropertyValues("mode")
                text_segmenter = TTSTextSegmenter(*key, mode=mode, **parameters[method])
            elif method == "speaker_diarization":
                p = parameters[method]
                p["endpoint"] = db.getPropertyValues("speaker_diarization_endpoint")
                text_segmenter = SpeakerDiarization(*key, **p)
            else:
                print("ERROR: Unknown text segmentation method: " + method)
                return

        self.set(key, text_segmenter)

        data["sender"] = name+":"+tag
        con.publish("mediator", session+"/"+tag, jsons.dumps(data)) # Send START to further components

    def on_end(self, session):
        return session.stop_working()

    def on_information(self, key):
        name, session, tag = key
        db.setContext(name,session,tag)

        print("Sending INFORMATION.")

        properties = db.getPropertyValues()
        print("PROPERTIES",properties)

        name += ":"+tag
        data={'session': session, 'controll': "INFORMATION", 'sender': name, name:properties}
        con.publish("mediator", session+"/"+tag, jsons.dumps(data))
        end_time = time.time()
        latency = end_time - self.start_time
        print("latency ",latency)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--queue-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--queue-port', help='NATS Server address', type=str, default='5672')
    parser.add_argument('--redis-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--redis-port', help='NATS Server address', type=str, default='6379')
    args = parser.parse_args()

    name = "textseg"

    db = get_best_database()(args.redis_server, args.redis_port)
    db.setDefaultProperties(property_to_default)

    con = get_best_connector()(args.queue_server,args.queue_port,db)
    # con.register(db, name, {})

    prep = TextSegmenterActiveSessionStorage(name, con, db)
    con.consume(name, prep, True)
