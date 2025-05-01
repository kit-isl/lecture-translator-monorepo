#!/usr/bin/env python3
import asyncio
import argparse
import json
import jsons

import httpx
import requests

import os
import subprocess
import tempfile
import base64
import time
import torch
import datetime
from collections import Counter
import random
import numpy as np
import math
from jiwer import wer

from multiprocessing.pool import ThreadPool as Pool

from qbmediator.Connection import get_best_connector
from qbmediator.Database import get_best_database
from qbmediator.Interface import MediatorInterface, ActiveSessionStorage
from qbmediator.Text import get_punctuation

from segmenter import DummySegmenter, SegmenterVAD, SegmenterSHAS, SegmenterSILERO
import kafka
import traceback

cosinesim = torch.nn.CosineSimilarity()

def video_to_pcm_s16le(video_bytes):
    with tempfile.NamedTemporaryFile() as tmp:
        tmp.write(base64.b64decode(video_bytes))
        # with open("ref.mp4", "wb") as f:
        #     f.write(base64.b64decode(video_bytes))
        tmp.flush()
        process = subprocess.run(("ffmpeg -hide_banner -loglevel warning -vn -i "+tmp.name+" -map 0:a -ac 1 -f s16le -ar 16000 -c:a pcm_s16le -").split(), stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        if process.returncode != 0:
            print("ERROR: Could not convert input to raw audio")
            return
        raw_wav = process.stdout
    return raw_wav

def pcm_s16le_to_tensor(pcm_s16le):
    audio_tensor = np.frombuffer(pcm_s16le, dtype=np.int16)
    audio_tensor = torch.from_numpy(audio_tensor)
    audio_tensor = audio_tensor.float() / math.pow(2, 15)
    audio_tensor = audio_tensor.unsqueeze(1)  # shape: frames x 1 (1 channel)
    return audio_tensor

class StreamingASR(MediatorInterface):
    def __init__(self, name, session, tag, mode, asr_server, segmenter_type, max_segment_length=20, user=None, speakerdiarization_following=False, *args, **kwargs):
        super().__init__(name, session, tag, mode)

        db.setContext(name, session, tag)

        self.asr_server_ = asr_server.split("|")
        self.asr_server_index = 0

        self.segmenter_type = segmenter_type

        self.forward_audio = db.getPropertyValues("forward_audio") == "True"
        self.compute_hyperarticulation = db.getPropertyValues("compute_hyperarticulation") == "True"
        self.output_exact_word_boundaries = db.getPropertyValues("output_exact_word_boundaries") == "True"

        if self.compute_hyperarticulation or self.output_exact_word_boundaries:
            self.runner = asyncio.Runner()
            self.httpx_async_client = httpx.AsyncClient()

        if self.segmenter_type == "SHAS":
            language = db.getPropertyValues("language")
            if "-" in language:
                language = language.split("-")[0]
            url = db.getPropertyValues("SHAS_segmenter").format(language)
            try:
                if requests.post(url).status_code == 404: # No SHAS segmenter found
                    print("WARNING: Could not find SHAS segmenter, using VAD as backup!")
                    raise RuntimeError
            except:
                self.segmenter_type = "VAD"

        send_signal = lambda signal: self.send_signal(signal)
        if self.segmenter_type == "None":
            self.segmenter = DummySegmenter(send_signal=send_signal)
        elif self.segmenter_type == "VAD":
            self.segmenter = SegmenterVAD(max_segment_length=max_segment_length, send_signal=send_signal)
        elif self.segmenter_type == "SILERO":
            self.segmenter = SegmenterSILERO(max_segment_length=max_segment_length, send_signal=send_signal)
        elif self.segmenter_type == "SHAS":
            min_segment_length = db.getPropertyValues("min_segment_length")
            self.segmenter = SegmenterSHAS(url, max_segment_length, min_segment_length)
        else:
            print("WARNING: Got unknown segmenter name, using VAD instead.")
            self.segmenter = SegmenterVAD(max_segment_length=max_segment_length, send_signal=send_signal)

        self.pause = False

        self.memory_words = None
        self.speaker_sample_id = None
        self.user = user
        self.speakerdiarization_following = speakerdiarization_following

    @property
    def asr_server(self):
        s = self.asr_server_[self.asr_server_index]
        self.asr_server_index += 1
        self.asr_server_index %= len(self.asr_server_)
        return s

    def stop_transcribing(self):
        print("Stopping transcribing.")
        self.running = False

        self.send_end()

    def append_signal(self):
        while True:
            audio = self.read_audio(return_start_time=True)
            if type(audio) is tuple:
                audio, start_time = audio
                if self.forward_audio and audio:
                    self.send_audio(audio, additional_dict={"start": start_time})
            else:
                start_time = None
                if self.forward_audio and audio:
                    self.send_audio(audio)
            if len(audio)==0:
                return
            if audio == b'PAUSE':
                print("RECEIVED PAUSE")
                self.segmenter.no_more_audio()
                self.pause = True
                return
            else:
                self.segmenter.append_signal(audio, start_time)

    def retrieve_speaker_embedding_id(self, speaker_sample_pcm_s16le, sample_name):
        asr_server_embed = "/".join(self.asr_server.split("|")[0].split("/")[:-2])+"/embed"

        r = requests.post(asr_server_embed, files={"pcm_s16le":speaker_sample_pcm_s16le}, verify=False)
        if r.status_code != 200:
            print("ERROR: Could not embed speaker sample!")
        else:
            self.speaker_sample_id = r.text

    def transcribe_audio(self, audio, prefix="", priority=0):
        #print("Requesting asr model for session {} using server {}...".format(self.session,self.asr_server))
        try:
            r = requests.post(self.asr_server, files={"pcm_s16le":audio, "prefix":prefix, "priority":priority, "memory":self.memory_words, "speaker_embedding_id":self.speaker_sample_id, "user":self.user}, verify=False)
            if r.status_code != 200:
                raise requests.exceptions.ConnectionError
        except requests.exceptions.ConnectionError:
            print(f"ERROR in asr model request to server {self.asr_server}, returning empty string.")
            return "", (None,None), ""
        r = r.json()
        #print(f"Got response from model {r}, tag {self.tag}")
        transcript = r["hypo"]
        confidence = (r["bpe_hypo"] if "bpe_hypo" in r else None,r["log_probs"] if "log_probs" in r else None)
        if not transcript.startswith(prefix):
            print("WARNING: Transcript does not start with prefix")
        lid = r["lid"] if "lid" in r else None
        return transcript, confidence, lid

    def send_text(self, *args, **kwargs):
        if self.speakerdiarization_following:
            if "additional_dict" not in kwargs:
                kwargs["additional_dict"] = {}
            if not "audio" in kwargs:
                if kwargs["stable"]:
                    print("WARNING: Audio forwarding requested but no audio given!")
            else:
                kwargs["additional_dict"]["audio"] = base64.b64encode(kwargs.pop("audio")).decode()
        if "audio" in kwargs:
            kwargs.pop("audio")

        super().send_text(*args, **kwargs)

    def transcribe_audio_and_send(self, segment, stable=False, segment_ends=False, segment_begins=False, priority=0):
        audio = segment.get_audio()
        text, confidence, lid = self.transcribe_audio(audio, priority=priority)

        start = segment.start_time()
        end = segment.end_time()

        self.send_text(start, end, text, stable=stable, segment_ends=segment_ends, segment_begins=segment_begins, confidence=confidence, additional_dict={"lid": lid}, audio=audio)

    def extract_words(self, pdf_bytes):
        server = self.asr_server
        server = "/".join(server.split("/")[:-2])+"/extract_words"
        try:
            r = requests.post(server, files={"pdf_bytes":pdf_bytes}, verify=False)
            if r.status_code != 200:
                raise requests.exceptions.ConnectionError

            r = r.json()
            words = r["memory_words"]
        except requests.exceptions.ConnectionError:
            print("ERROR in asr model extract words request, returning empty list.")
            words = []
        self.send_data("memory_words", words, encode=False, force_linking=False, send_link=False)

    async def compute_additional_features(self, audio, text, start, end):
        additional_features = {}

        text_len = len(text.split())

        if self.compute_hyperarticulation:
            data = {"transcript": text, "start": start,  "end": end}
            files = {'pcm_s16le': ('pcm_s16le.wav', audio, 'audio/wave')}
            hyperarticulation_response_awaitable = asyncio.create_task(self.httpx_async_client.post(
                'http://192.168.0.64:8090/asr/infer/None,None',
                data=data, files=files, timeout=3 + 0.5 * text_len))

        if self.output_exact_word_boundaries:
            data = {"transcript": text, "start": start,  "end": end}
            files = {'pcm_s16le': ('pcm_s16le.wav', audio, 'audio/wave')}
            output_exact_word_boundaries_response_awaitable = asyncio.create_task(self.httpx_async_client.post(
                'http://192.168.0.64:8091/asr/infer/None,None',
                data=data, files=files, timeout=3 + 0.3 * text_len))

        if self.compute_hyperarticulation:
            try:
                response = await hyperarticulation_response_awaitable
                additional_features['hyperarticulation'] = response.json() if response.status_code == 200 else []
            except (httpx.ConnectError, httpx.TimeoutException):
                print('Server error of hyperarticulation finder')
                additional_features['hyperarticulation'] = []

        if self.output_exact_word_boundaries:
            try:
                response = await output_exact_word_boundaries_response_awaitable
                additional_features['output_words'] = response.json() if response.status_code == 200 else []
            except (httpx.ConnectError, httpx.TimeoutException):
                print('Server error of exact word boundaries finder')
                additional_features['output_words'] = []

        return additional_features

    def send_pause(self):
        print("Sending Pause.", self.session, self.tag)
        data={'session': self.session, 'controll': "PAUSE", 'sender': self.name+":"+self.tag}
        self.con.publish("mediator", self.session+"/"+self.tag, jsons.dumps(data))

    def send_signal(self, signal):
        data = {"session": self.session, "sender": self.name + ":" + self.tag, "signal":signal}
        self.con.publish("mediator", self.session + "/" + self.tag, jsons.dumps(data))

class OfflineASR(StreamingASR):
    def __init__(self, *args, dummy_asr="False", **kwargs):
        super().__init__(*args, **kwargs)

        self.dummy_asr = dummy_asr

        self.finished = False
        self.segments = []

        self.next_callback = None

    def process(self):
        self.append_signal()

        if not self.running:
            self.stop_transcribing()

    def transcribe_audio(self, *args, **kwargs):
        if self.dummy_asr == "True":
            return "-------------------------------------------- no transcript, just dummy output --------------------------------------------", None, None
        return super().transcribe_audio(*args, **kwargs)

    def stop_transcribing(self):
        if self.finished:
            return

        if self.running:
            print("Starting transcribing.",self.session,self.tag)

            self.segmenter.no_more_audio() # necessary for example for the SHAS segmenter used in offline mode
        
            self.running = False

        while True:
            segment = self.segmenter.active_seg()
            if segment is None:
                break

            self.segments.append(segment)
            segment.finish()

        use_callbacks = False
        segments = self.segments
        if use_callbacks:
            segments_max = 8
            segments = segments[:segments_max]

        with Pool() as p: # work on multiple segments at the same time
            for segment, (text,confidence,lid) in zip(segments, p.imap(self.transcribe_audio, [segment.get_audio() for segment in segments])):
                start = segment.start_time()
                end = segment.end_time()

                self.send_text(start, end, text, stable=True, segment_ends=True, segment_begins=True, confidence=confidence, additional_dict={"lid": lid}, audio=segment.get_audio())

                self.segments = self.segments[1:]

        if len(self.segments) == 0:
            self.send_end()
            self.finished = True
        else:
            self.next_callback = time.time()
            print("SET CALLBACK")
            return False # Do not delete session

    def send_end(self): # No process to prevent recursion cycle
        print("Sending END.", self.session, self.tag)
        data={'session': self.session, 'controll': "END", 'sender': self.name+":"+self.tag}
        self.con.publish("mediator", self.session+"/"+self.tag, jsons.dumps(data))

class StreamingASRwithoutStabilityDetection(StreamingASR):
    def process(self):
        self.append_signal()
        self.transcribe()
        if self.pause:
            self.send_pause()
            self.pause = False

    def stop_transcribing(self):
        if self.running:
            self.running = False
            self.append_signal()
            self.transcribe(send_unchanged=True)
            self.send_end()

    def transcribe(self, send_unchanged=False):
        while True:
            segment = self.segmenter.active_seg(send_unchanged=send_unchanged)
            if segment is None:
                break

            completed = segment.completed or not self.running

            self.transcribe_audio_and_send(segment, stable=completed, segment_ends=completed, segment_begins=True, priority=1)
            if completed:
                segment.finish()
                print("Finished segment.")
            else:
                break

# based on https://arxiv.org/pdf/2204.06028.pdf
class StreamingASRwithStabilityDetectionLocalAgreement2(StreamingASRwithoutStabilityDetection):
    def __init__(self, *args, LA2_chunk_size=1, LA2_max_context=20, LA2_max_words_unstable=20, **kwargs):
        super().__init__(*args, **kwargs)

        self.chunk_size = LA2_chunk_size # in seconds; chunk_size times 32000 has to be divisible by 2 because two bytes is one frame
        self.max_context = LA2_max_context # in seconds
        self.max_words_unstable = LA2_max_words_unstable
        self.punctuation = get_punctuation()

        self.next_segment()

    def next_segment(self):
        self.start_time_within_segment = 0
        self.stop_time_within_segment = self.chunk_size
        self.first_within_segment = True
        self.sent_something_stable = False
        self.last_stable_end_time = 0
        self.last_text = ""
        self.prefix = ""
        self.stable_info = []
        self.no_new_stable = 0

    def get_stable_prefix(self, text, stable=True):
        prefix = os.path.commonprefix([self.last_text, text]).strip()

        if not ((len(self.last_text)>len(prefix) and not self.last_text[len(prefix)].isalpha()) and (len(text)>len(prefix) and not text[len(prefix)].isalpha())):
            while prefix and prefix[-1].isalpha():
                prefix = prefix[:-1]
            prefix = prefix.rstrip()

        if len(prefix) < len(self.prefix):
            prefix = self.prefix

        num_space = len([c for c in text[len(prefix):] if c==" "]) - self.max_words_unstable
        if num_space > 0:
            print("WARNING: Forcing words to be stable")
            while len(prefix) < len(text):
                c = text[len(prefix)]
                prefix += c
                if c == " ":
                    num_space -= 1
                    if num_space == -1:
                        break
            prefix = prefix.rstrip()

        new_prefix = text[len(self.prefix):len(prefix)]
        #print(f"SEARCH FOR STABLE OUTPUT:\n{self.last_text = }\n{text           = }\n{prefix         = }\n{new_prefix     = }")
        return prefix, new_prefix

    def postprocess_text(self, text):
        text = text.rstrip()
        for p in self.punctuation:
            text = text.rstrip(p)
        return text

    def transcribe_audio_and_send(self, segment, stable=False, segment_ends=False, segment_begins=False, priority=0):
        start = segment.start_time()
        end = segment.end_time()
        length = segment.size()

        audio = segment.get_audio(start=self.start_time_within_segment)

        if not stable:
            if length < self.stop_time_within_segment: # not enough new audio yet
                if self.mode == "SendUnstable":
                    text_inter, confidence, lid = self.transcribe_audio(audio, prefix=self.prefix, priority=1)
                    unstable_suffix_inter = text_inter[len(self.prefix):]
                    #self.send_text(start+self.last_stable_end_time, end, unstable_suffix_inter, stable=False, segment_ends=False, segment_begins=not self.sent_something_stable, confidence=confidence, additional_dict={"lid": lid})
                return

            if self.first_within_segment:
                self.first_within_segment = False

                text, confidence, lid = self.transcribe_audio(audio, priority=1)

                self.send_text(start, start+length, text, stable=False, segment_ends=False, segment_begins=not self.sent_something_stable, confidence=confidence, additional_dict={"lid": lid})

                self.stable_info.append([0,""])
            else:
                text, confidence, lid = self.transcribe_audio(audio, prefix=self.prefix, priority=1)

                prefix, new_prefix = self.get_stable_prefix(self.postprocess_text(text))
                unstable_suffix = text[len(prefix):]
                print(f"{         prefix = }\n{unstable_suffix = }")

                if len(new_prefix) > 0:
                    if self.compute_hyperarticulation or self.output_exact_word_boundaries:
                        audio_to_process = segment.get_audio(start=self.last_stable_end_time, end=self.last_stop_time_within_segment)
                        additional_features = self.runner.run(self.compute_additional_features(
                            audio_to_process, new_prefix, start+self.last_stable_end_time,
                            start+self.last_stop_time_within_segment))

                    if self.compute_hyperarticulation:
                        hyperarticulation = additional_features['hyperarticulation']

                    if self.output_exact_word_boundaries and additional_features['output_words']:
                        output_words = additional_features['output_words']

                        for i, (output_word_start, output_word_end, output_word) in enumerate(output_words):
                            if self.compute_hyperarticulation and i in hyperarticulation:
                                output_word = f'<i>{output_word}</i>'
                            self.send_text(output_word_start, output_word_end, output_word, stable=True,
                                           segment_ends=False, segment_begins=i == 0 and not self.sent_something_stable,
                                           confidence=confidence, additional_dict={"lid": lid}, audio=segment.get_audio(start=output_word_start-start, end=output_word_end-start))
                    else:
                        new_text = (' '.join(f'<i>{word}</i>' if i in hyperarticulation else word for i, word in enumerate(new_prefix.split()))
                                    if self.compute_hyperarticulation
                                    else new_prefix)
                        self.send_text(start+self.last_stable_end_time, start+self.last_stop_time_within_segment, new_text,
                                       stable=True, segment_ends=False, segment_begins=not self.sent_something_stable,
                                       confidence=confidence, additional_dict={"lid": lid}, audio=segment.get_audio(start=self.last_stable_end_time, end=self.last_stop_time_within_segment))

                    self.sent_something_stable = True

                    self.send_text(start+self.last_stop_time_within_segment, start+length, unstable_suffix, stable=False, segment_ends=False, segment_begins=not self.sent_something_stable, confidence=confidence, additional_dict={"lid": lid})

                    self.stable_info.append([self.last_stop_time_within_segment,new_prefix])

                    self.last_stable_end_time = self.last_stop_time_within_segment
                else:
                    self.send_text(start+self.last_stable_end_time, start+length, unstable_suffix, stable=False, segment_ends=False, segment_begins=not self.sent_something_stable, confidence=confidence, additional_dict={"lid": lid})

                self.prefix = prefix

            self.last_text = text
            self.last_stop_time_within_segment = length

            self.stop_time_within_segment = length + self.chunk_size
            while self.stop_time_within_segment - self.start_time_within_segment > self.max_context and len(self.stable_info) > 0:
                e,p = self.stable_info.pop(0)
                self.start_time_within_segment = e
                self.prefix = self.prefix[len(p):].strip()
                self.last_text = self.last_text[len(p):].strip()
        else:
            text, confidence, lid = self.transcribe_audio(audio, prefix=self.prefix, priority=1)
            text = text[len(self.prefix):]

            if self.output_exact_word_boundaries or self.compute_hyperarticulation:
                audio_to_process = segment.get_audio(start=self.last_stable_end_time, end=end-start)
                additional_features = self.runner.run(self.compute_additional_features(
                    audio_to_process, text, start+self.last_stable_end_time, end))

            if self.compute_hyperarticulation:
                hyperarticulation = additional_features['hyperarticulation']

            if self.output_exact_word_boundaries and additional_features['output_words']:
                output_words = additional_features['output_words']
                for i, (output_word_start, output_word_end, output_word) in enumerate(output_words):
                    if self.compute_hyperarticulation and i in hyperarticulation:
                        output_word = f'<i>{output_word}</i>'
                    self.send_text(output_word_start, output_word_end, output_word, stable=True,
                                   segment_ends=i == len(output_words) - 1,
                                   segment_begins=i == 0 and not self.sent_something_stable, confidence=confidence, additional_dict={"lid": lid},
                                   audio=segment.get_audio(start=output_word_start, end=output_word_end))
            else:
                if self.compute_hyperarticulation:
                    text = ' '.join(f'<i>{word}</i>' if i in hyperarticulation else word for i, word in enumerate(text.split()))
                additional_dict = {"lid": lid}
                self.send_text(start+self.last_stable_end_time, end, text, stable=True,
                               segment_ends=True, segment_begins=not self.sent_something_stable,
                               confidence=confidence, additional_dict=additional_dict, audio=segment.get_audio(start=self.last_stable_end_time, end=end))

            self.next_segment()

class ReadAllText(StreamingASR):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.messages = []

    def process(self):
        while True:
            mes = self.read_text()
            if mes is None:
                break
            if len(self.messages) == 0 or not self.messages[-1].get("unstable", False):
                self.messages.append(mes)
            else:
                self.messages[-1] = mes
        if self.messages:
            self.process_()
            self.messages = [mes for mes in self.messages if not mes.get("finished", False)]

class StreamingASRNumbersReformatting(ReadAllText):
    def __init__(self, name, session, tag, mode, asr_server, *args, **kwargs):
        super().__init__(name, session, tag, mode, asr_server, segmenter_type=None, max_segment_length=None, user=None, *args, **kwargs)

        self.punctuation = get_punctuation()
        self.punctuation2 = [","]

        self.reset()

    def reset(self):
        self.start = None
        self.seq = ""
        self.audio = bytes()
        self.lid = None
        self.speech_segment_begins = False
        self.audio_lengths = []
        self.text_lengths = []

    def reformat_numbers(self, text, unstable):
        #print("Requesting asr model for session {} using server {}...".format(self.session,self.asr_server))
        try:
            r = requests.post(self.asr_server, {"text":text, "unstable":unstable}, verify=False)
            if r.status_code != 200:
                raise requests.exceptions.ConnectionError
        except requests.exceptions.ConnectionError:
            print(f"ERROR in asr model request to server {self.asr_server}, returning empty string.")
            return ""
        result = r.text
        return result

    def process_(self):
        for mes in self.messages:
            mes["finished"] = True

            start_ = mes.get("start", 0)
            end_ = mes.get("end", 1)
            lid_ = mes.get("lid", "")
            seq_ = mes.get("seq", "")
            speech_segment_begins_ = mes.get("speech_segment_begins", False)
            speech_segment_ends_ = mes.get("speech_segment_ends", False)

            unstable = mes.get("unstable")
            if unstable:
                break

            if self.start is None:
                self.start = start_
                self.lid = lid_
                self.speech_segment_begins = speech_segment_begins_

            self.seq += seq_

            audio_ = mes.get("audio")
            if audio_:
                self.audio += base64.b64decode(audio_)
                self.audio_lengths.append(len(audio_))
                self.text_lengths.append(len(seq_.strip().split()))
                
            while True:
                if not self.seq:
                    break

                indices = split2 = None
                if speech_segment_ends_:
                    indices = [len(self.seq)]
                if not indices:
                    indices = [index for p in self.punctuation if (index := self.seq.find(p+" ")+1)]
                if not indices and self.seq[-1] in self.punctuation:
                    indices = [len(self.seq)]
                if not indices and len(self.audio) > 32000*10:
                    indices = [(index,p) for p in self.punctuation2 if (index := self.seq.rfind(p+" ")+1)]
                    if indices:
                        index = max(indices, key=lambda x: x[0])
                        indices, split2 = [index[0]-1], index[1]

                if not indices:
                    break
                index = min(indices)

                end = self.start + index * (end_-self.start) / len(self.seq)
                seq_send, self.seq = self.seq[:index], self.seq[index:]
                middle = len(self.seq) > 0

                if self.audio:
                    if middle:
                        num_words = len(seq_send.strip().split())
                        audio_send = bytes()
                        while self.text_lengths and num_words >= 0:
                            audio_send += self.audio[self.audio_lengths[0]:]
                            self.audio = self.audio[:self.audio_lengths[0]]
                            num_words -= self.text_lengths[0]
                            self.audio_lengths = self.audio_lengths[1:]
                            self.text_lengths = self.text_lengths[1:]
                    else:
                        audio_send, self.audio = self.audio, bytes()
                else:
                    audio_send = bytes()
                speech_segment_ends = speech_segment_ends_ and not middle

                if self.lid in ["en","de"]:
                    seq_send = self.reformat_numbers(seq_send, unstable=False)
                if split2:
                    self.seq = self.seq[1:]
                    seq_send += split2

                if seq_send:
                    audio_send = base64.b64encode(audio_send).decode()
                    self.send_text(self.start, end, seq_send, stable=True, segment_ends=speech_segment_ends, segment_begins=self.speech_segment_begins, additional_dict={"lid": self.lid, "audio": audio_send})

                self.start = end
                self.speech_segment_begins = False
                if speech_segment_ends:
                    self.reset()

        if self.messages[-1].get("unstable"):
            self.send_text(start_, end_, self.seq+seq_, stable=False, segment_ends=False, segment_begins=speech_segment_begins_, additional_dict={"lid": lid_ if not self.lid else self.lid})

class SAStreamingASR(ReadAllText):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.punctuation = get_punctuation()
        self.session_data = dict()
        self.reset()

    def reset(self):
        self.start = None
        self.done_prefix = ""
        self.not_done_prefix = ""
        self.audio = bytes()
        self.audio_lengths = []
        self.text_lengths = []

    def process_(self):
        last_unstable = self.messages[-1].get("unstable")

        for i,mes in enumerate(self.messages):
            mes["finished"] = True

            if self.start is None:
                self.start = mes.get("start")
                self.lid = mes.get("lid")
            speech_segment_ends = mes.get("speech_segment_ends")
            last_message = i == len(self.messages) - 1

            unstable = mes.get("unstable")
            if not unstable:
                new_prefix = mes.get("seq")
                self.not_done_prefix += new_prefix

            audio_ = mes.get("audio")
            if audio_:
                self.audio += base64.b64decode(audio_)

                if not unstable:
                    self.audio_lengths.append(len(audio_))

                    while len(self.audio) > 32000*20 and self.audio_lengths:
                        self.audio = self.audio[self.audio_lengths[0]:]
                        self.audio_lengths = self.audio_lengths[1:]
                    while self.text_lengths and len(self.done_prefix.strip().split()) >= self.text_lengths[0] and len((self.done_prefix+self.not_done_prefix).strip().split()) > 60:
                        self.done_prefix = " ".join(self.done_prefix.strip().split()[self.text_lengths[0]:])
                        self.text_lengths = self.text_lengths[1:]

            if speech_segment_ends or (last_message and not last_unstable):
                #print(f"Using {self.done_prefix+self.not_done_prefix = } as speaker diarization input")
                transcript, spk_embed = self.get_speakerembeddings(self.audio, self.done_prefix+self.not_done_prefix, self.lid)

                if spk_embed is not None:
                    index = len(self.done_prefix.split())
                    not_done_prefix = " ".join(transcript.split()[index:])
                    spk_embed = spk_embed[index:]

                    if len(not_done_prefix.strip()) > 0:
                        cluster_name = self.assign_word_cluster(not_done_prefix, spk_embed)
                    else:
                        spk_embed = cluster_name = None
                else:
                    spk_embed = cluster_name = None
                    not_done_prefix = self.not_done_prefix

                if not_done_prefix.strip():
                    data = self.get_data(mes)
                    data["start"] = self.start
                    self.start = data["end"]
                    data["text"] = not_done_prefix
                    self.send_text(**data, spk_embed=spk_embed, text_spk_cluster_name=cluster_name)

                if self.done_prefix and not_done_prefix and (self.done_prefix[-1] != " " or not_done_prefix[0] != " "):
                    self.done_prefix += " "
                self.done_prefix += not_done_prefix
                self.text_lengths.append(len(not_done_prefix.strip().split()))
                self.not_done_prefix = ""

                if speech_segment_ends:
                    self.reset()
            elif last_message and last_unstable:
                unstable_suffix = mes.get("seq")

                if self.not_done_prefix:
                    #print(f"Using {self.done_prefix+self.not_done_prefix+unstable_suffix = } as speaker diarization input")
                    transcript, spk_embed = self.get_speakerembeddings(self.audio, self.done_prefix+self.not_done_prefix+unstable_suffix, self.lid)

                    if spk_embed is not None:
                        index = len(self.done_prefix.split())
                        not_done_prefix = transcript.split()[index:]
                        spk_embed = spk_embed[index:]
                        index2 = len(unstable_suffix.split())
                        if index2 > 0:
                            spk_embed = spk_embed[:-index2]
                            not_done_prefix = not_done_prefix[:-index2]
                        not_done_prefix = " ".join(not_done_prefix)

                        if len(not_done_prefix.strip()) > 0:
                            cluster_name = self.assign_word_cluster(not_done_prefix, spk_embed)
                        else:
                            spk_embed = cluster_name = None
                    else:
                        spk_embed = cluster_name = None
                        not_done_prefix = self.not_done_prefix

                    if not_done_prefix.strip():
                        data = self.get_data(mes)
                        data["end"] = data["start"]
                        data["start"] = self.start
                        self.start = data["end"]
                        data["stable"] = True
                        data["text"] = not_done_prefix
                        self.send_text(**data, spk_embed=spk_embed, text_spk_cluster_name=cluster_name)

                    if self.done_prefix and not_done_prefix and (self.done_prefix[-1] != " " or not_done_prefix[0] != " "):
                        self.done_prefix += " "
                    self.done_prefix += not_done_prefix
                    self.text_lengths.append(len(not_done_prefix.strip().split()))
                    self.not_done_prefix = ""

                data = self.get_data(mes)
                data["text"] = unstable_suffix
                self.send_text(**data)
        
        self.refine_sentence_cluster()
        
        
    def get_data(self, mes):
        data = {key:mes.get(key) for key in ['start', 'end', 'confidence']}
        data["stable"] = not mes.get("unstable")
        data["additional_dict"] = {"lid": mes.get("lid")}
        data["segment_ends"] = mes.get("speech_segment_ends")
        data["segment_begins"] = mes.get("speech_segment_begins")
        return data

    def update_stable_info(self, text, embed, spk_cluster_name):
        words = text.split()
        assert len(words) == len(embed)
        start_id = len(self.session_data[self.session][self.tag]["words"])
        self.session_data[self.session][self.tag]["words"].extend(words)
        self.session_data[self.session][self.tag]["embed"].extend(embed)
        self.session_data[self.session][self.tag]["assigned_cluster"].extend([spk_cluster_name] * len(words))
        self.session_data[self.session][self.tag]["refined_sentence"].extend([False] * len(words))
        
        for word_id in range(start_id, len(self.session_data[self.session][self.tag]["words"])):
            # self.session_data[self.session][self.tag]["words"][word_id] = self.session_data[self.session][self.tag]["words"][word_id].strip()
            db.setPropertyValue('word-'+str(word_id), json.dumps({
                "word": self.session_data[self.session][self.tag]["words"][word_id],
                "embed": self.session_data[self.session][self.tag]["embed"][word_id].numpy().tolist(),
            }), context=[self.name, self.session, self.tag])
            print("Update word:", self.session_data[self.session][self.tag]["words"][word_id], "word_id:", word_id, "into:", db.context, "key:", 'word-'+str(word_id))
        return words, list(range(start_id, len(self.session_data[self.session][self.tag]["words"])))

    def get_session_embedding_data(self):
        if self.session_data.get(self.session, None) is None:
            self.session_data[self.session] = {}
        if self.session_data[self.session].get(self.tag, None) is None:
            self.session_data[self.session][self.tag] = {
                "words": [],
                "embed": [],
                "assigned_cluster": [],
                "saved_cluster": {},
                "saved_cluster_embedding": {},
                "refined_sentence": []
            }
        
        try:
            data_spk_cluster = db.getPropertyValues("spk-cluster", context=[self.name, self.session, "speakerdiarization"])
            retrieved_cluster = json.loads(data_spk_cluster)
            print("List speaker in database:", {key: len(value) for key, value in retrieved_cluster.items()})
            # Need to update reference spk embedding 
            if self.session_data[self.session][self.tag]["saved_cluster"] != retrieved_cluster:
                print("New version of reference. Update reference speaker cluster value")
                self.session_data[self.session][self.tag]["saved_cluster"] = retrieved_cluster
                for key, value in retrieved_cluster.items():
                    for word_id in value:
                        if self.session_data[self.session][self.tag]["saved_cluster_embedding"].get(word_id, None) is None:
                            try:
                                word_context_name, word_context_session, word_context_tag, word_context_key = word_id.split("/")
                                
                                word_embed = torch.tensor(json.loads(db.getPropertyValues(word_context_key, context=[word_context_name, 
                                                                                                        word_context_session, 
                                                                                                        word_context_tag]))['embed'])
                                self.session_data[self.session][self.tag]["saved_cluster_embedding"][word_id] = word_embed
                            except:
                                print("ERROR: Can't get word embedding from", word_id)
                                #traceback.print_exc()
            retrieved_cluster_embedding = {}
            for key, value in retrieved_cluster.items():
                if len(value) > 0:
                    retrieved_cluster_embedding[key] = torch.stack([self.session_data[self.session][self.tag]["saved_cluster_embedding"][word_id] for word_id in value])
        except:
            print(data_spk_cluster)
            #traceback.print_exc()
            retrieved_cluster_embedding = {}
        
        return retrieved_cluster_embedding

    def assign_word_cluster(self, text, embed, cluster_threshold=0.5):
        
        retrieved_cluster_embedding = self.get_session_embedding_data()
        
        # for each embed, calculate the similarity with all cluster
        # if similarity > threshold, assign to cluster
        # if similarity < threshold, return unk
        assigned_clusters = []
        for word, vector in zip(text.split(), embed):
            most_similar_cluster = None
            most_similar_score = 0
            for cluster, cluster_embedding in retrieved_cluster_embedding.items():
                similarity = cosinesim(vector.unsqueeze(0), cluster_embedding)
                similarity = torch.topk(similarity, min(5, len(similarity))).values.mean().item()
                if similarity > most_similar_score:
                    most_similar_score = similarity
                    most_similar_cluster = cluster
            print("word:", word, "most_similar_cluster", most_similar_cluster, "most_similar_score", most_similar_score)
            if most_similar_score > cluster_threshold:
                assigned_clusters.append(most_similar_cluster)
            else:
                assigned_clusters.append('unk')
        
        ###### try handling when only unk spk #####
        try:
            last_word_embed = None if len(self.session_data[self.session][self.tag]["embed"]) == 0 else self.session_data[self.session][self.tag]["embed"][-1]
            last_word_cluster = self.session_data[self.session][self.tag]["assigned_cluster"][-1] if last_word_embed is not None else None
            if last_word_cluster is not None:
                unk_clusters = [cluster for cluster in self.session_data[self.session][self.tag]["assigned_cluster"] if cluster.startswith('unk-')]
                if len(unk_clusters) > 0:
                    last_unk_cluster = unk_clusters[-1]
                else:
                    last_unk_cluster = None
            else:
                last_unk_cluster = None
                
            for idx, cluster in enumerate(assigned_clusters):
                if cluster == 'unk':
                    if last_word_cluster is None or last_unk_cluster is None:
                        assigned_clusters[idx] = 'unk-0'
                    else:
                        if last_word_cluster.startswith('unk-'):
                            cosin_sim = cosinesim(embed[idx].unsqueeze(0), last_word_embed.unsqueeze(0)).item()
                            
                            if cosin_sim > cluster_threshold:
                                assigned_clusters[idx] = last_word_cluster
                            else:
                                print("Speaker change detected for unk spk. Similarity:", cosin_sim, "threshold:", cluster_threshold)
                                assigned_clusters[idx] = f'unk-{int(last_unk_cluster.split("-")[1])+1}'
                        else:
                            assigned_clusters[idx] = f'unk-{int(last_unk_cluster.split("-")[1])+1}'
                    last_unk_cluster = assigned_clusters[idx]
                    
                last_word_embed = embed[idx]
                last_word_cluster = assigned_clusters[idx]
            ###### end handling when only unk spk #####
        except:
            print("ERROR: Can't handle unk spk")
            #traceback.print_exc()
        
        return assigned_clusters
    
    def refine_sentence_cluster(self):
        try:
            ############## handling spk by sentence boundary ##############
            words = self.session_data[self.session][self.tag]["words"]
            assigned_cluster = self.session_data[self.session][self.tag]["assigned_cluster"]
            refined_sentence = self.session_data[self.session][self.tag]["refined_sentence"]
            # print("=====> refined_sentence", refined_sentence)
            sentences = []
            current_sentence = []
            for idx, (word, cluster, refined) in enumerate(zip(words, assigned_cluster, refined_sentence)):
                if not refined:
                    if word[-1] in self.punctuation:
                        current_sentence.append((idx, word, cluster))
                        sentences.append(current_sentence)
                        # update refined sentence
                        for word_data in current_sentence:
                            # print("=====> Refine word_data: ", word_data)
                            self.session_data[self.session][self.tag]["refined_sentence"][word_data[0]] = True
                        current_sentence = []
                    else:
                        current_sentence.append((idx, word, cluster))
            
            for sentence in sentences:
                sentence_text = " ".join([word_data[1] for word_data in sentence])
                sentence_cluster = [word_data[2] for word_data in sentence]
                all_unk_spk = True
                
                for word_clust_name in sentence_cluster:
                    if not word_clust_name.startswith('unk-'):
                        all_unk_spk = False
                        break
                if all_unk_spk:
                    print("All unk spk in sentence. Skip refine sentence cluster")
                    print("=====> Refine sentence: ", sentence_text)
                    continue
                    
                refined_sentence_cluster = max(set(sentence_cluster), key=sentence_cluster.count)
                sentence_word_id = [word_data[0] for word_data in sentence]
                
                data = {
                    "session":self.session, 
                    "sender": self.name+":"+self.tag, 
                    "unstable": False,
                    "sentence": sentence_text,
                    "sentence_word_id": sentence_word_id,
                    "refined_sentence_cluster": refined_sentence_cluster
                }
                self.con.publish("mediator", self.session+"/"+self.tag, jsons.dumps(data))
                print("=====> Refine sentence: ", sentence_text, "cluster:", refined_sentence_cluster, "cluster_before: ", set(sentence_cluster))
                
        except:
            print("WARNING: Can't refine sentence cluster")
            # traceback.print_exc()
    
    def send_text(self, start, end, text: str, spk_embed=None, text_spk_cluster_name=None, stable=False, segment_ends=False, segment_begins=False, confidence=None, additional_dict=None):
        if self.mode != "SendUnstable" and not stable:
            return
        
        if text_spk_cluster_name is not None:
            if len(set(text_spk_cluster_name)) > 1:
                print("WARNING: Multiple speaker cluster in one segment. Estimate start end of each segment")
                # estimate start end of each segment
                total_length = end - start
                total_char = sum([len(word) for word in text.split()])
                char_duration = total_length / total_char
                word_duration = [len(word) * char_duration for word in text.split()]
                
                segments = []
                current_segment = []
                current_start = start
                for word, word_embed, duration, cluster_name in zip(text.split(), spk_embed, word_duration, text_spk_cluster_name):
                    if len(current_segment) == 0:
                        current_segment.append([word, duration, cluster_name, word_embed])
                    elif current_segment[0][2] == cluster_name:
                        current_segment.append([word, duration, cluster_name, word_embed])
                    else:
                        current_end = current_start + sum([item[1] for item in current_segment])
                        current_segment_text = " ".join([item[0] for item in current_segment])
                        current_segment_embed = [item[3] for item in current_segment]
                        segments.append((current_segment_text, current_start, current_end, current_segment_embed, current_segment[0][2]))
                        
                        current_segment = [[word, duration, cluster_name, word_embed]]
                        current_start = current_end
                if len(current_segment) > 0:
                    current_end = current_start + sum([item[1] for item in current_segment])
                    current_segment_text = " ".join([item[0] for item in current_segment])
                    current_segment_embed = [item[3] for item in current_segment]
                    segments.append((current_segment_text, current_start, current_end, current_segment_embed, current_segment[0][2]))
            else:
                segments = [(text, start, end, spk_embed, text_spk_cluster_name[0])]
        else:    
            segments = [(text, start, end, spk_embed, 'unk')]
        
        print("number of segments", len(segments))
            
        for i in range(len(segments)):
            text,start,end,embed,spk_cluster_name = segments[i]
            
            if embed is not None and stable:
                words, word_id = self.update_stable_info(text, embed, spk_cluster_name)
            else:
                words = None
                word_id = None
            
            data = {"session":self.session, "sender": self.name+":"+self.tag, "start": start, "end": end, "seq": text, "unstable": not stable, "speech_segment_ends": segment_ends}

            if additional_dict is not None:
                data.update(additional_dict)
            
            session_info = {
                # "spk_list": self.ref_name,
                "words": words,
                "word_id": word_id,
                "speaker": f"{self.session}:{self.name}:{self.tag}",
                "speakerName": f"{self.session}:{self.name}:{self.tag}:{spk_cluster_name}"
            }
            
            data.update(session_info)
            # print("send_data", data)
            self.con.publish("mediator", self.session+"/"+self.tag, jsons.dumps(data))

    def get_speakerembeddings(self, audio, transcript, lid):
        try:
            print("Send request to: ", self.spk_embed_server + f"{lid},None")
            r = requests.post(self.spk_embed_server + f"{lid},None", files={"pcm_s16le":audio, "transcript": transcript}, verify=False)
            if r.status_code != 200:
                raise requests.exceptions.ConnectionError
            r_json = r.json()
            _, _, [words, spk_embed] = r_json["hypo"], r_json["lid"], r_json["saasr"]
            spk_embed = torch.tensor(spk_embed)
        except:
            #traceback.print_exc()
            print("ERROR in asr model request, returning empty string.")
            return "", None
        return transcript, spk_embed
    
property_to_default = {
    "mode": "SendStable", # Options: SendUnstable, SendStable
    "asr_server_default": "http://192.168.0.60:5052/asr/infer/None,None",
    "spk_embed_server": "http://192.168.0.60:5024/asr/infer/",
    "version": "online", # Options: online, offline
    "segmenter": "SILERO", # Options: None, VAD, SILERO, SHAS (only for offline)
    "stability_detection": "True", # Options: False, True
    "max_segment_length": "20",
    "min_segment_length": "1", # for the SHAS segmenter
    "language": "en",
    "SHAS_segmenter": "http://192.168.0.60:5010/segmenter/{}/infer",
    "LA2_chunk_size": 1,
    "LA2_max_context": 20,
    "LA2_max_words_unstable": 10,
    "display_language": "en",
    "dummy_asr": "False", # Options: False, True, used when only segmentation should be returned
    "use_speaker_attribute_extraction": "False",
    "forward_audio": "False",
    "compute_hyperarticulation": "False",
    "output_exact_word_boundaries": "False",
    "numbers_reformatting": "False",
    }

class ASRActiveSessionStorage(ActiveSessionStorage):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, use_callbacks=True, **kwargs)

    def on_start(self, key, data):
        name, session, tag = key

        value = self.get(key)
        if value is not None:
            value.stop_transcribing() # stop old one

        db.setContext(name, session, tag)

        # Read properties
        mode = db.getPropertyValues("mode")
        asr_server = self.get_asr_server()
        
        try:
            saasr_status = int(db.getPropertyValues("saasr")) == 1
            print("saasr_status", type(saasr_status), saasr_status)
        except Exception as e:
            #print("ERROR: Could not get saasr status")
            saasr_status = False
            
        numbers_reformatting = db.getPropertyValues("numbers_reformatting") == "True"
        speakerdiarization_following = db.getPropertyValues("speakerdiarization_following") == "True"

        spk_embed_server = db.getPropertyValues("spk_embed_server")
        version = db.getPropertyValues("version")
        segmenter_type = db.getPropertyValues("segmenter")
        max_segment_length = float(db.getPropertyValues("max_segment_length"))
        use_speaker_attribute_extraction = db.getPropertyValues("use_speaker_attribute_extraction") == "True"
        user = data.get("host")

        # Instanciate subclass of SteamingASR class
        if numbers_reformatting:
            value = StreamingASRNumbersReformatting(name, session, tag, mode, asr_server)
        elif version=="online":
            stability_detection = db.getPropertyValues("stability_detection")
            if segmenter_type == "SHAS":
                print("ERROR: SHAS is currently only available for version offline, not starting session.")
                return

            if stability_detection=="False":
                value = StreamingASRwithoutStabilityDetection(name, session, tag, mode, asr_server, segmenter_type, max_segment_length=max_segment_length, user=user, speakerdiarization_following=speakerdiarization_following)
            else:
                LA2_chunk_size = float(db.getPropertyValues("LA2_chunk_size"))
                LA2_max_context = float(db.getPropertyValues("LA2_max_context"))
                LA2_max_words_unstable = float(db.getPropertyValues("LA2_max_words_unstable"))
                c = StreamingASRwithStabilityDetectionLocalAgreement2
                if saasr_status or use_speaker_attribute_extraction:
                    print("Using SAASR", "saasr_status", saasr_status, "use_speaker_attribute_extraction", use_speaker_attribute_extraction)
                    c = SAStreamingASR
                    
                value = c(name, session, tag, mode, asr_server, segmenter_type, max_segment_length=-1, LA2_chunk_size=LA2_chunk_size, LA2_max_context=LA2_max_context, LA2_max_words_unstable=LA2_max_words_unstable, user=user, speakerdiarization_following=speakerdiarization_following)
                if isinstance(value, SAStreamingASR):
                    value.spk_embed_server = spk_embed_server
        elif version=="offline":
            dummy_asr = db.getPropertyValues("dummy_asr")

            value = OfflineASR(name, session, tag, mode, asr_server, segmenter_type, max_segment_length=max_segment_length, dummy_asr=dummy_asr, user=user, speakerdiarization_following=speakerdiarization_following)
        else:
            print("ERROR: Found unknown version, not starting session.")
            return

        # Start decoding thread
        self.set(key, value)

        
        # if "sample_name" in data: # Which speaker sample to use
        #     sample_name = data["sample_name"]
        #     if sample_name is None or sample_name == "None":
        #         print("ERROR: Could not extract speaker information!")
        #         return
        #     print("Loading sample name",sample_name)
        #     video_bytes = db.db.hget('facedubbing_videos', sample_name)
        #     speaker_sample_pcm_s16le = video_to_pcm_s16le(video_bytes)
        #     value.retrieve_speaker_embedding_id(speaker_sample_pcm_s16le, sample_name)
        # else:
        #     print("WARNING: No sample_name given, not using speaker attribute extraction.")

        data["sender"] = name+":"+tag
        if "send_to" in data:
            del data["send_to"]
        con.publish("mediator", session+"/"+tag, jsons.dumps(data)) # Send START to further components

    def on_end(self, session):
        return session.stop_transcribing()

    def on_information(self, key):
        name, session, tag = key
        
        db.setContext(name, session, tag)

        # Do ASR server version request
        url = self.get_asr_server(version=True)

        properties = db.getPropertyValues()
        print("PROPERTIES",properties)

        try:
            res = requests.post(url, verify=False)
            if res.status_code != 200:
                raise requests.exceptions.ConnectionError
        except requests.exceptions.ConnectionError:
            print("WARNING: Could not request asr_server version, ignoring.")
            version = "Request failed"
        else:
            version = res.text

        properties["asr_server_version"] = version

        name += ":"+tag
        data={'session': session, 'controll': "INFORMATION", 'sender': name, name:properties}
        con.publish("mediator", session+"/"+tag, jsons.dumps(data))

    def process(self, body):
        try:
            res = super().process(body)
        except Exception as e:
            print("ERROR: Could not process session", e)
            return
        if res is None:
            return
        key, data = res

        if "memory_words" in data:
            session = self.get(key)
            if session is not None:
                session.memory_words = data["memory_words"]
            else:
                print("WARNING: Could not set memory words.")
        if "pdffile" in data:
            session = self.get(key)
            if session is not None:
                session.extract_words(data["pdffile"])
            else:
                print("WARNING: Could not extract memory words from pdffile.")
        if "keyboard_press" in data or "keyboard_release" in data:
            print(data)
                
        if "audio_reference" in data:
            # session = data["session"]
            # tag = data["tag"]
            # name = data.get("name", "asr")
            # # print("key", list(self.sessions.keys()))
            # saasr = self.get((name,session,tag))
            print("key", key)
            session = self.get(key)
            print("session", session)
            try:
                if session is not None and isinstance(session, SAASR):
                    audio_reference = base64.b64decode(data["audio_reference"])
                    spk_name = data["spk_name"]
                    print("audio_reference", len(audio_reference))
                    session.retrieve_speaker_embedding_id(audio_reference, spk_name)
                    # session.send_data({"audio_reference": "received", "spk_name": spk_name})
            except Exception as e:
                print("ERROR: Could not retrieve speaker embedding", e)
                return
            
        if "remove_speaker_embedding_by_name" in data:
            session = self.get(key)
            if session is not None and isinstance(session, SAASR):
                remove_spk_name = data["remove_speaker_embedding_by_name"]
                print("remove_spk_name", remove_spk_name)
                session.remove_speaker_embedding_by_name(remove_spk_name)
        
        
        return key, data

    def get_asr_server(self, version=False):
        language = db.getPropertyValues("language")
        asr_server = db.getPropertyValues("asr_server_"+language)
        print(f"{language = }, {asr_server = }")
        if asr_server is None:
            asr_server = db.getPropertyValues("asr_server_default")
            print("WARNING: Unknown language",language,", using default asr_server",asr_server)
        if version:
            url = "/".join(asr_server.split("/")[:-2])+"/version"
            return url
        return asr_server

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--queue-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--queue-port', help='NATS Server address', type=str, default='5672')
    parser.add_argument('--redis-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--redis-port', help='NATS Server address', type=str, default='6379')
    args = parser.parse_args()

    name = "asr"

    db = get_best_database()(args.redis_server, args.redis_port)
    db.setDefaultProperties(property_to_default)

    con = get_best_connector()(args.queue_server,args.queue_port,db)
    con.register(db, name, {})

    sessions = ASRActiveSessionStorage(name, con, db)
    while True:
        try:
            con.consume(name, sessions, True)
        except kafka.errors.CommitFailedError:
            print("ERROR: kafka.errors.CommitFailedError, ignoring")
