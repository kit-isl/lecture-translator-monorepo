#!/usr/bin/python

import collections
import webrtcvad
import time

import torch
torch.set_num_threads(1)
import numpy as np
import math

import requests

from abc import ABC, abstractmethod

def frame_generator(audio, length_of_frame, start_time):
    """Generates audio frames from PCM audio data.

    Takes the desired frame duration in milliseconds, the PCM data, and
    the sample rate.

    Yields Frames of the requested duration.
    """
    offset = 0

    while offset + length_of_frame <= len(audio):
        yield Frame(audio[offset:offset + length_of_frame], start_time)
        start_time += length_of_frame
        offset += length_of_frame

class Frame(object):
    """Represents a "frame" of audio data."""
    def __init__(self, bytes, timestamp):
        self.bytes = bytes
        self.timestamp = timestamp # in frames

class AudioSegment():
    def __init__(self, start_time, data=bytes()):
        self.time = start_time # in frames
        self.data = data
        self.active = True
        self.completed = False

    def start_time(self): # in seconds
        return self.time / 32000

    def end_time(self): # in seconds
        return self.start_time() + self.size()

    def append(self, bytes):
        self.data += bytes

    def complete(self):
        self.completed = True

    def finish(self):
        self.active = False

    def get_audio(self, start=None, end=None): # start and end in seconds
        data = self.data
        if end is not None:
            index = int(32000*end)
            if index %2 != 0:
                index -= 1
            data = data[:index]
        if start is not None:
            index = int(32000*start)
            if index %2 != 0:
                index += 1
            data = data[index:]
        return data

    def size(self): # in seconds
        return len(self.data) / 32000

class Segmenter(ABC):
    def __init__(self, **kwargs):
        self.start_time = 0
        self.segs = []
        self.segment = None
        self.last_returned = None

        self.send_signal = kwargs["send_signal"] if "send_signal" in kwargs else None

    def active_seg(self, send_unchanged=False):
        for pos, seg in enumerate(self.segs):
            if seg.active:
                if not send_unchanged:
                    if self.last_returned == (pos, seg.size(), seg.completed):
                        return None
                    self.last_returned = (pos, seg.size(), seg.completed)
                return seg
        return None

    @abstractmethod
    def append_signal(self, audio:bytes, start_time=None): # audio: pcm_s16le byte stream
        if start_time is not None:
            self.start_time = start_time

    def no_more_audio(self):
        if self.segment is not None and not self.segment.completed:
            self.segment.complete()
            if self.send_signal is not None:
                self.send_signal("speech_segment_end")

class DummySegmenter(Segmenter):
    def append_signal(self, audio, start_time=None):
        super().append_signal(audio, start_time)
        if self.segment is None:
            self.segment = AudioSegment(self.start_time)
            if self.send_signal is not None:
                self.send_signal("speech_segment_start")
        self.segment.append(audio)
        self.segs.append(self.segment)
        self.start_time += len(audio)

class SegmenterSHAS(Segmenter):
    def __init__(self, url, max_segment_length, min_segment_length):
        super().__init__()

        self.url = url
        self.max_segment_length = max_segment_length
        self.min_segment_length = min_segment_length
        self.segment_ = None

    def append_signal(self, audio, start_time=None):
        super().append_signal(audio, start_time)
        if self.segment is None:
            self.segment = AudioSegment(self.start_time)
            self.segment_ = AudioSegment(self.start_time)
        self.segment_.append(audio)

    def no_more_audio(self):
        if self.segment is None:
            print("WARNING: Did not receive any audio to segment in SHAS segmenter")
            return

        audio = self.segment_.data

        if len(audio) % 2 != 0: # has to be even
            audio = audio[:-1]

        print("Requesting SHAS segmenter for segment of size {}...".format(self.segment_.size()))
        r = requests.post(self.url, files={"audio":audio, "max_segment_length":str(self.max_segment_length), "min_segment_length":str(self.min_segment_length)})
        if r.status_code != 200:
            print("ERROR in SHAS model request")
        else:
            segmentation = r.json()
            print("Segmentation:",segmentation)
            for s,e in segmentation:
                s = int(32000*float(s)) # some padding
                if s % 2 != 0: # have to start with even number because two bytes is one frame
                    s += 1
                e = int(32000*float(e)) # some padding
                if e % 2 != 0: # have to also end with an even number
                    e -= 1
                audio_ = audio[s:e]
                segment = AudioSegment(self.start_time+s, data=audio_)
                segment.complete()
                print("Created segment with length",segment.size())
                self.segs.append(segment)

        self.start_time += len(audio)

        super().no_more_audio()

def pcm_s16le_to_tensor(pcm_s16le):
    audio_tensor = np.frombuffer(pcm_s16le, dtype=np.int16)
    audio_tensor = torch.from_numpy(audio_tensor)
    audio_tensor = audio_tensor.float() / math.pow(2, 15)
    return audio_tensor

def tensor_to_pcm_s16le(audio_tensor):
    audio_tensor = (audio_tensor * math.pow(2, 15)).to(torch.int16)
    audio_tensor = audio_tensor.numpy()
    audio_tensor = audio_tensor.tobytes()
    return audio_tensor

class SegmenterSILERO(Segmenter):
    def __init__(self, max_segment_length=20, **kwargs):
        super().__init__(**kwargs)

        model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                                      model='silero_vad',
                                      force_reload=False,
                                      onnx=True)

        (get_speech_timestamps,
         save_audio,
         read_audio,
         VADIterator,
         collect_chunks) = utils

        self.max_segment_length = max_segment_length

        self.vad_iterator = VADIterator(model, min_silence_duration_ms=2000)
        self.window_size_samples = 512 # number of samples in a single audio chunk

        self.temp = torch.empty(0)
        self.triggered = False
        self.current_segment_length = 0

    def append_signal(self, pcm_s16le, start_time=None):
        super().append_signal(pcm_s16le, start_time)

        tensor = torch.cat([self.temp,pcm_s16le_to_tensor(pcm_s16le)])

        for i in range(0, len(tensor), self.window_size_samples):
            chunk = tensor[i:i+self.window_size_samples]
            if len(chunk) < self.window_size_samples:
                self.temp = chunk
                break

            speech_dict = self.vad_iterator(chunk, return_seconds=True)

            if speech_dict is not None and "start" in speech_dict:
                self.triggered = True
                self.segment = AudioSegment(32000*self.start_time)
                self.segs.append(self.segment)
                self.current_segment_length = 0
                if self.send_signal is not None:
                    self.send_signal("speech_segment_start")

            if self.triggered:
                self.segment.append(tensor_to_pcm_s16le(chunk))
                self.current_segment_length += self.window_size_samples/16000

            if speech_dict is not None and "end" in speech_dict:
                self.triggered = False
                self.segment.complete()
                if self.send_signal is not None:
                    self.send_signal("speech_segment_end")
            elif self.max_segment_length > 0 and self.current_segment_length + self.window_size_samples/16000 >= self.max_segment_length:
                self.current_segment_length = 0
                self.segment.complete()
                if self.send_signal is not None:
                    self.send_signal("speech_segment_end")

                self.segment = AudioSegment(32000*self.start_time)
                self.segs.append(self.segment)
                if self.send_signal is not None:
                    self.send_signal("speech_segment_start")

            self.start_time += self.window_size_samples/16000

    def no_more_audio(self):
        self.temp = torch.empty(0)
        self.triggered = False
        self.current_segment_length = 0
        self.vad_iterator.reset_states()
        super().no_more_audio()

class SegmenterVAD(Segmenter):
    def __init__(self, sample_rate=16000, VAD_aggressive=2, padding_duration_ms=1000, frame_duration_ms=30, rate_begin=0.65, rate_end=0.55, max_segment_length=20, **kwargs):
        super().__init__(**kwargs)

        self.vad = webrtcvad.Vad(VAD_aggressive)
        self.padding_duration_ms = padding_duration_ms
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.triggered = False
        self.length_of_frame = int(self.sample_rate * self.frame_duration_ms / 1000.0 * 2) # 2 because 2 bytes is one frame
        self.rate_begin = rate_begin
        self.rate_end = rate_end
        self.max_segment_length = max_segment_length

        num_padding_frames = int(self.padding_duration_ms / self.frame_duration_ms)
        self.ring_buffer = collections.deque(maxlen=num_padding_frames)

        self.triggered = False
        self.temp = bytes()
        self.ring_buffer.clear()

    def append_signal(self, audio, start_time=None):
        super().append_signal(audio, start_time)

        audio = self.temp + audio

        length_audio = len(audio) // self.length_of_frame * self.length_of_frame

        self.temp = audio[length_audio:]
        audio = audio[:length_audio]

        for j, frame in enumerate(frame_generator(audio, self.length_of_frame, 32000*self.start_time)):
            is_speech = self.vad.is_speech(frame.bytes, self.sample_rate)
            if not self.triggered:
                self.ring_buffer.append((frame, is_speech))
                num_voiced = len([f for f, speech in self.ring_buffer if speech])

                if num_voiced >  self.rate_begin * self.ring_buffer.maxlen:
                    self.triggered = True
                    self.segment = AudioSegment(self.ring_buffer[0][0].timestamp)
                    for f, s in self.ring_buffer:
                        self.segment.append(f.bytes)
                    self.segs.append(self.segment)
                    print('New segment added.')
                    if self.send_signal is not None:
                        self.send_signal("speech_segment_start")
                    self.ring_buffer.clear()
            else:
                self.segment.append(frame.bytes)
                self.ring_buffer.append((frame, is_speech))
                num_unvoiced = len([f for f, speech in self.ring_buffer if not speech])
                if num_unvoiced >  self.rate_end * self.ring_buffer.maxlen or (self.max_segment_length>0 and self.segment.size() > self.max_segment_length):
                    self.triggered = False
                    self.segment.complete()
                    if self.send_signal is not None:
                        self.send_signal("speech_segment_end")
                    self.ring_buffer.clear()

        self.start_time = self.start_time + len(audio) / 32000

    def no_more_audio(self):
        self.triggered = False
        self.ring_buffer.clear()
        self.temp = bytes()
        super().no_more_audio()

