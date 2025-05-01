#!/usr/bin/env python3
import argparse
import base64
import json
import re
import time
import threading
import httpx
import requests
import jsons
import subprocess
import tempfile
import os
import pysbd
import wave
import numpy as np
import math

import struct
import tempfile
import shutil

from qbmediator.Connection import get_best_connector
from qbmediator.Database import get_best_database
from qbmediator.Interface import MediatorInterface, ActiveSessionStorage

property_to_default = {
    "tts_server": "http://192.168.0.68:5057/tts/infer/", # http://i13hpc62:5054/tts/infer/schwabish
    "tts_server_multi": "http://192.168.0.68:5057/tts/infer/multi",
    "tts_server_e2tts": "http://192.168.0.62:5053/tts/infer/",
    "language":"en",
    # "languages": "en,de,fr,zh",
    "languages": "en,de,fr,es,zh,ja,fa,ar,vi,th,tr,pl,ru,uk,ko,pt,hi,bn",
    "language_mapping": {
        "en":"eng", "de":"de_DE", "fr":"fr_FR", "es":"es_ES", "zh":"zh_CN", "ja":"ja_JP", \
        "fa":"per", "ar":"ara", "vi":"vie", "th":"tha", "tr":"tur", "pl":"pol", "ru":"rus", \
        "uk":"ukr", "ko":"kor", "pt":"por", "hi":"hin", "bn":"ben"
    },
    # "language_mapping": {
    #     "en":"eng", "de":"deu", "fr":"fra", "es":"es_ES", "zh":"chn", "ja":"ja_JP", \
    #     "fa":"per", "ar":"ara", "vi":"vie", "th":"tha", "tr":"tur", "pl":"pol", "ru":"rus", \
    #     "uk":"ukr", "ko":"kor", "pt":"por", "hi":"hin", "bn":"ben"
    # },
    # "language_mapping": {"en":"eng","de":"deu","fr":"fra","zh":"chn"},
    "display_language": "en_audio",
    "len_scale": "1",
    "version": "online", # Options: offline
    "generate_silence": "False",
    "realtime_factor": "2",
    "audio_length_voice_conversion": "3",
    "mode": "SendRevision", # Options: SendPartial(for fixed mode), SendBot(for Bot Dialogue)
    "voice_profile": "False"
}

class OfflineStreamingTTS(MediatorInterface):
    def __init__(self, name, session, tag, tts_server, tts_server_multi, language, len_scale, generate_silence, audio_length_voice_conversion, voice_profile, mode, *args, timeout=None, **kwargs):
        super().__init__(name, session, tag, *args, **kwargs)
        self.tts_server = tts_server
        self.language = language
        self.len_scale = len_scale
        self.generate_silence = generate_silence
        self.audio_length_voice_conversion = audio_length_voice_conversion
        self.voice_profile = voice_profile
        self.tts_server_multi = tts_server_multi
        self.mode = mode

        self.audio = bytes()
        self.last_end = None

        self.timeout = timeout
        self.seg = pysbd.Segmenter(language='en', clean=True)
        self.sample_rate = 16000  # 16 kHz
        self.bytes_per_sample = 2  # Assuming 16-bit audio
        self.num_channels = 1
        
        # Audio Buffer
        self.audio_buffer = b''
        self.chunk_duration = 4 #depending on the bottleneck of model inference speed along the pipeline

        self.chunk_size = int(self.chunk_duration * self.sample_rate * self.bytes_per_sample)
        self.audio_saved_buffer_list = []

        self.waibel_voice_profile = None
        # self.initialize_voice_profile()

        self.m3u8 = tempfile.NamedTemporaryFile(suffix=".m3u8")
        self.base = "/".join(self.m3u8.name.split("/")[:-1])
        
        self.line_next = 0
        self.ref_text = None
        self.last_info = {}
        self.word_archive = {}
        
    def convert_to_ts(self, video):
        with tempfile.NamedTemporaryFile("wb") as temp:
            temp.write(b'RIFF\xf0\xb2\x1e\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00LIST\x1a\x00\x00\x00INFOISFT\x0e\x00\x00\x00Lavf58.29.100\x00data\xaa\xb2\x1e\x00')
            temp.write(video)
            temp.flush()
            #cmd = "ffmpeg -hide_banner -loglevel warning -i "+temp.name+" -f hls -hls_time 1 -hls_list_size 0 -hls_flags append_list+split_by_time -hls_playlist_type event "+self.m3u8.name
            cmd = "ffmpeg -hide_banner -loglevel warning -i "+temp.name+" -f hls -hls_time 1 -hls_list_size 0 -hls_flags append_list "+self.m3u8.name
            proc = subprocess.run(cmd.split())
        
    def initialize_voice_profile(self):
        try:
            wav_path = "voice.wav"
            if not os.path.exists(wav_path):
                raise FileNotFoundError(f"WAV file not found: {wav_path}")
            
            audio_list = self.process_wav_to_pcm(wav_path)
            self.waibel_voice_profile = self.list_to_pcm_s16le(audio_list)
        except Exception as e:
            print(f"Error initializing voice profile: {str(e)}")
            self.waibel_voice_profile = None

    def process_wav_to_pcm(self, file_path):
        # Open the WAV file
        with wave.open(file_path, 'rb') as wav_file:
            # Extract parameters
            sample_rate = wav_file.getframerate()
            num_channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            num_frames = wav_file.getnframes()

            # Read frames and convert to numpy array
            raw_data = wav_file.readframes(num_frames)
            audio_samples = np.frombuffer(raw_data, dtype=np.int16)  # Assuming PCM S16LE format

            # Normalize audio samples to range [-1, 1]
            audio_samples = audio_samples / float(2**15)

            # If stereo, average channels (convert to mono)
            if num_channels > 1:
                audio_samples = audio_samples.reshape(-1, num_channels).mean(axis=1)
        return audio_samples
    
    def list_to_pcm_s16le(self, audio_list):
        audio_list= np.array(audio_list)
        audio_list = (audio_list * math.pow(2, 15)).astype(np.int16)
        audio_list = audio_list.tobytes()
        audio_list = base64.b64encode(audio_list).decode("ascii")
        return audio_list
    
    def send_live_video(self, pcm_s16le, send_video_additional_dict={}):
        self.convert_to_ts(pcm_s16le)
        print("sending audio: ", len(pcm_s16le))
        # self.send_audio(pcm_s16le, additional_dict=send_video_additional_dict)
        with open(self.m3u8.name,"r") as f:
            
            lines = f.readlines()
            lines = lines[self.line_next:]
        
            for line in lines:
                line = line.strip()
                if line == "#EXT-X-ENDLIST":
                    break
                if line.startswith("#EXTINF"):
                    length = float(line[len("#EXTINF:"):-1])
                    send_video_additional_dict['length'] = length
                elif not line.startswith("#"):
                    with open(self.base+"/"+line,"rb") as f2:
                        output_video = f2.read()
                    self.send_video(output_video, additional_dict=send_video_additional_dict)
                self.line_next += 1
                
    def process(self):
        if not self.running:
            return
        try:
            data = self.read_text()
        except:
            data = None
            
        # if not data:
        #     return
        # if "unstable" in data and data["unstable"]:
        #     return
        # if not "seq" in data or not data["seq"].strip():
        #     return

        # RECEIVED PAUSE
        # TODO and to move to consecutive TTS later:
        if data is not None:
            print(data)
            
        # Create TTS
        if data is not None and data["unstable"] is False and data["sender"].startswith(('mt', 'textseg:tts', 'bot')):
            start, end, seq = float(data["start"]) if "start" in data else 0, float(data["end"]) if "end" in data else 1, data["seq"]

            if seq.strip() != "":
                segments = re.split('[,]', seq)
                segments = [segment.strip() for segment in segments if segment.strip()]
                # audio buffer
                for seg in segments:
                    print("#"*50)
                    print(f"{self.language} Text Input for continuity TTS: ", seg)
                    # self.send_text(start, end, "start running TTS", stable=True)

                    if self.language in ["en", "bn"]:
                        if self.last_info:
                            prev_text, prev_dur, prev_pitch, prev_energy = self.last_info["text"], self.last_info["durs"], self.last_info["pitch"], self.last_info["energy"]
                        else:
                            prev_text, prev_dur, prev_pitch, prev_energy = "", None, None, None

                        cur_sen = prev_text +'<\s>'+seg
                        pcm_s16le, durs, f0, energy, prev_text = self.flow_synthesize_speech(bytes(), cur_sen, language=self.language, len_scale=1.0, dr_seg=prev_dur, pitch_seg=prev_pitch, energy_seg=prev_energy)
                        # self.last_info["text"], self.last_info["durs"], self.last_info["pitch"], self.last_info["energy"] = seq, np.log(1.0+durs), f0, energy
                        #
                        self.last_info["text"], self.last_info["durs"], self.last_info["pitch"], self.last_info["energy"] = prev_text, np.log(1.0+durs), f0, energy
                    else:
                        pcm_s16le = self.synthesize_speech(bytes(), seg, language=self.language, len_scale=1.0, ref_text=None)
                    
                seconds = len(pcm_s16le)/(self.sample_rate*self.bytes_per_sample)
                self.send_live_video(pcm_s16le)
                """
                if self.mode=="SendBot":
                    self.send_continuity_audio(pcm_s16le, {"length": seconds, "tts-start": start, "tts-end": end, "transcript": seq})
                else:
                    self.send_live_video(pcm_s16le, {"length": seconds, "tts-start": start, "tts-end": end, "transcript": seq})
                """
                # self.send_text(start, end, "finish running TTS", stable=True)
                print("SENT AUDIO",seconds,"for session",self.session,"and tag",self.tag)
                print("#"*50)
                """
                if self.mode=="SendBot":
                    self.send_continuity_remaining_audio(send_video_additional_dict={})
                else:
                    self.send_remaining_video(send_video_additional_dict={})
                """

    def send_continuity_audio(self, pcm_s16le, send_video_additional_dict):
        self.audio_buffer += pcm_s16le
        print(f"{self.language} adding segments...")
        if len(self.audio_buffer) >= self.chunk_size:
            while len(self.audio_buffer) >= self.chunk_size:
                print(f"{self.language} buffer limit reached...")
                chunk = self.audio_buffer[:self.chunk_size]
                self.audio_buffer = self.audio_buffer[self.chunk_size:]
                self.chunk_counter += 1
                self.audio_saved_buffer_list.append(chunk)
                self.send_audio(chunk,  additional_dict=send_video_additional_dict)

    def send_continuity_remaining_audio(self, send_video_additional_dict=None):
        chunk = self.audio_buffer
        audio_len = len(chunk)/(self.sample_rate * self.bytes_per_sample)
        self.audio_buffer = b''
        self.chunk_counter += 1
        send_video_additional_dict['length'] = audio_len
        self.audio_saved_buffer_list.append(chunk)
        self.send_audio(chunk, additional_dict=send_video_additional_dict)

    def write_wav_with_dynamic_header(self, audio_data, output_path):
        """
        Writes audio data to a WAV file with a dynamically calculated header.
    
        :param audio_data: The raw audio data (bytes).
        :param sample_rate: The sample rate (e.g., 16000 for 16 kHz).
        :param num_channels: Number of audio channels (1 for mono, 2 for stereo).
        :param bits_per_sample: Number of bits per sample (e.g., 16 for 16-bit audio).
        :param output_path: Path where the WAV file will be written.
        """
        audio_data_size = len(audio_data)  # Size of raw audio data
        byte_rate = self.sample_rate * self.num_channels * self.bytes_per_sample  # Bytes per second
        block_align = self.num_channels * self.bytes_per_sample # Block align (bytes per frame)
        riff_size = 4 + (8 + 16) + (8 + audio_data_size)  # Total size minus the RIFF descriptor (8 bytes)

        # Create the WAV header dynamically
        wav_header = struct.pack(
            '<4sI4s4sIHHIIHH4sI',
            b'RIFF', riff_size, b'WAVE',  # RIFF chunk descriptor
            b'fmt ', 16, 1, self.num_channels, self.sample_rate, byte_rate, block_align, self.bytes_per_sample*8,  # fmt chunk
            b'data', audio_data_size  # data chunk descriptor
        )

        # Write the WAV file
        with open(output_path, 'wb') as wav_file:
            wav_file.write(wav_header)  # Write the header
            wav_file.write(audio_data)  # Write the audio data

        print(f"WAV file written to {output_path}")

    def stop_working(self):
        if not self.running:
            return

        self.running = False
        print("Starting tts.")
        
        self.synthesize_speech_and_send()
        self.send_end()
    
    def pcm_to_wav(self, pcm_data, sample_rate, output_filename):
        # Set up WAV parameters
        num_channels = 1  # Mono
        sample_width = 2  # 16-bit
        sample_rate = 16000 

        # Create WAV file
        with wave.open(output_filename, 'wb') as wav_file:
            wav_file.setnchannels(num_channels)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_data)

    def chunk_to_wav(self, pcm_chunks):
        # Convert each PCM chunk to WAV
        for index, pcm_chunk in enumerate(pcm_chunks):
            output_filename = f"segmentation_debug_{index}.wav"
            self.pcm_to_wav(pcm_chunk, sample_rate, output_filename)
    
    # def send_end(self):
    #     self.chunk_to_wav(self.audio_saved_buffer_list)
    #     super.send_end()
    
    def flow_synthesize_speech(self, audio, text, language=None, len_scale=1.0, dr_seg=None, pitch_seg=None, energy_seg=None):
        # tts_server = "http://192.168.0.62:5057/tts/flow_infer/"
        tts_server = "http://192.168.0.62:5058/tts/flow_infer/"
        timeout = None
        # make bengali slower
        if language_mapping.get(language)=="ben":
            len_scale = 1.2

        try:
            # Sending a POST request using the requests library
            response = requests.post(
                    tts_server+language_mapping.get(language),
                    files={
                        "pcm_s16le": audio,
                        "seq": text,
                        "len_scale": str(len_scale),
                        "dr_seg": np.array(dr_seg, dtype=np.float32).tobytes() if dr_seg is not None else None,
                        "pitch_seg": np.array(pitch_seg, dtype=np.float32).tobytes() if pitch_seg is not None else None,
                        "energy_seg": np.array(energy_seg, dtype=np.float32).tobytes() if energy_seg is not None else None,
                        "streaming": "true"
                    },
                    timeout=timeout
            )
            # Check if the response status code is not 200
            if response.status_code != 200:
                raise requests.ConnectionError(
                    "HTTP return code of TTS model request is not equal to 200.")
        except requests.ConnectionError:
            print("ERROR in TTS model request, returning empty string.")
            return b''
        except requests.Timeout:
            print("TIMEOUT in TTS model request, returning empty string.")
            return b''
        else:
            # Process the response
            audio = base64.b64decode(response.json()["audio"])
            while len(audio) >= 2 and audio[:2] == b'\x00\x00':
                audio = audio[2:]
            while audio[-2:] == b'\x00\x00':
                audio = audio[:-2]
            dr_pred = np.array(response.json()["dr_pred"], dtype=np.float32)
            pitch_pred = np.array(response.json()["f0_pred"], dtype=np.float32)
            energy_pred = np.array(response.json()["energy_pred"], dtype=np.float32)
            if "text" in response.json().keys():
                return audio, dr_pred, pitch_pred, energy_pred, response.json()["text"]
            return audio, dr_pred, pitch_pred, energy_pred,

    def synthesize_speech(self, audio, text, language=None, len_scale=1.0, ref_text=None):
        try:
            # Sending a POST request using the requests library
            if language == None or self.mode=="SendBot":
                response = requests.post(
                    self.tts_server_multi,
                    files={
                        "pcm_s16le": audio,
                        # "ref_text": ref_text,
                        "seq": text,
                        "len_scale": str(len_scale),
                        # "sr": str(16000)
                    },
                    timeout=self.timeout
                )
            else:
                if language_mapping.get(language)=="ben":
                    len_scale = 1.3
                else:
                    len_scale = 1.0
                    
                response = requests.post(
                    self.tts_server+language_mapping.get(language),
                    files={
                        "pcm_s16le": audio,
                        # "ref_text": ref_text,
                        "seq": text,
                        "len_scale": str(len_scale),
                        # "sr": str(16000)
                    },
                    timeout=self.timeout
                )

            # Check if the response status code is not 200
            if response.status_code != 200:
                raise requests.ConnectionError("HTTP return code of TTS model request is not equal to 200.")
        except requests.ConnectionError:
            print("ERROR in TTS model request, returning empty string.")
            return b''
        except requests.Timeout:
            print("TIMEOUT in TTS model request, returning empty string.")
            return b''
        else:
            # Process the response
            audio = base64.b64decode(response.json()["audio"])
            while len(audio) >= 2 and audio[:2] == b'\x00\x00':
                audio = audio[2:]
            while audio[-2:] == b'\x00\x00':
                audio = audio[:-2]
            return audio

    def get_silence(self, seconds):
        frame = b'\x00\x00'
        pcm_s16le = int(16000*seconds)*frame
        return pcm_s16le

    def update_audio(self):
        try:
            audio = self.read_audio()
            self.audio += audio
        except:
            pass

        if len(self.audio) > 32000*self.audio_length_voice_conversion:
            self.audio = self.audio[-32000*self.audio_length_voice_conversion:]

    def synthesize_speech_and_send(self):
        self.update_audio()

        while True:
            data = self.read_text()
            if data is None:
                break
            start, end, seq = float(data["start"]), float(data["end"]), data["seq"]

            if self.generate_silence:
                if self.last_end is None:
                    silence = self.get_silence(start)
                    self.send_audio(silence)
                    #print("SENT STARTING SILENCE",start)
                    self.last_end = start
                elif self.last_end < start:
                    silence = self.get_silence(start-self.last_end)
                    self.send_audio(silence)
                    #print("SENT BETWEEN SILENCE",start-self.last_end)
                    self.last_end = start

            if seq.strip() != "":
                pcm_s16le = self.synthesize_speech(self.audio, seq, self.language, self.len_scale)
                self.send_audio(pcm_s16le)
                seconds = len(pcm_s16le)/32000
            else:
                output_audio = self.get_silence(end-start)
                self.send_audio(output_audio)
                seconds = end-start
                
            if self.generate_silence:
                self.last_end += seconds
                
    def after_receive_pause(self):
        pass

class ConsecutiveStreamingTTS(OfflineStreamingTTS):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.text_per_turn = ''
        self.audio_per_turn = b''
        self.segmenter = pysbd.Segmenter(language='en', clean=True)
        
    def bengali_segmenter(self, text):
        segments = re.split(r'\s*[|।.,]\s*', text)
        return [segment.strip() for segment in segments if segment.strip()]
    
    def normal_segmenter(self, text):
        sens = self.segmenter.segment(text)
        segments = []
        for sen in sens:
            segments += re.split('[,]', sen)
        return segments
    
    def text_to_pcm(self, text):
        print("#"*50)
        print(f"{self.language} Text Input for continuity TTS: ", text)
        if self.language in ["en", "bn"]:
            if self.last_info:
                prev_text, prev_dur, prev_pitch, prev_energy = self.last_info["text"], self.last_info["durs"], self.last_info["pitch"], self.last_info["energy"]
            else:
                prev_text, prev_dur, prev_pitch, prev_energy = "", None, None, None
            cur_sen = prev_text +'<\s>'+text
            pcm_s16le, durs, f0, energy, prev_text = self.flow_synthesize_speech(bytes(), cur_sen, language=self.language, len_scale=1.0, dr_seg=prev_dur, pitch_seg=prev_pitch, energy_seg=prev_energy)
            self.last_info["text"], self.last_info["durs"], self.last_info["pitch"], self.last_info["energy"] = prev_text, np.log(1.0+durs), f0, energy
        else:
            pcm_s16le = self.synthesize_speech(bytes(), text, language=self.language, len_scale=1.0, ref_text=None)
                    
        seconds = len(pcm_s16le)/(self.sample_rate*self.bytes_per_sample)
        # self.send_live_video(pcm_s16le)
        print("CREATE AUDIO",seconds,"for session",self.session,"and tag",self.tag)
        print("#"*50)
        return pcm_s16le
        
    def process(self):
        if not self.running:
            return
        try:
            data = self.read_text()
        except:
            data = None
            
        if not data:
            return
        if "unstable" in data and data["unstable"]:
            return
        if not "seq" in data or not data["seq"].strip():
            return

        # print('data: ', data)
        if data["sender"].startswith(('mt', 'textseg:tts', 'bot')):
            # print('data: ', data)
            start, end, seq = float(data["start"]) if "start" in data else 0, float(data["end"]) if "end" in data else 1, data["seq"].strip()
            if self.text_per_turn:
                # to combine the text we need a blank in between
                self.text_per_turn+=' '+seq
            else:
                self.text_per_turn = seq
            
            sens = self.bengali_segmenter(self.text_per_turn) if self.language =='bn' else self.normal_segmenter(self.text_per_turn)
            
            if len(sens) == 0:
                return
            
            if len(sens) > 1 or sens[-1].endswith((".", ",", "?", "!", "।")):
                if sens[-1].endswith((".", ",", "?", "!", "।")):
                    self.text_per_turn =''
                    sens_to_create = sens
                else:
                    self.text_per_turn = sens[-1]
                    sens_to_create = sens[:-1]
                # now TTS inference for sentences:
                for seg in sens_to_create:
                    pcm_s16le = self.text_to_pcm(seg)
                    self.audio_per_turn += pcm_s16le
                    
    def after_receive_pause(self):
        print('PAUSE RECEIVED !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!')
        # Option 1:
        if self.text_per_turn:
            pcm_s16le = self.text_to_pcm(self.text_per_turn)
            self.audio_per_turn += pcm_s16le

        if self.audio_per_turn:
            self.send_audio(self.audio_per_turn)
            self.send_live_video(self.audio_per_turn)
        # Option2:
        # if self.audio_per_turn:
        #     self.send_audio(self.audio_per_turn)
        # if self.text_per_turn:
        #     pcm_s16le = self.text_to_pcm(self.text_per_turn)
        #     self.audio_per_turn += pcm_s16le
        # if self.audio_per_turn:
        #     self.send_audio(self.audio_per_turn)
        self.audio_per_turn = b''
        self.text_per_turn = ''


class OnlineStreamingTTS(OfflineStreamingTTS):
    def __init__(self, *args, realtime_factor=2, **kwargs):
        super().__init__(*args, **kwargs)

        print("*"*50)
        print("self.mode: ", self.mode)
        print("*"*50)
        self.realtime_factor = realtime_factor # generation of x seconds of tts/video takes 1 seconds

        self.have_audio_until = None
        self.next_callback = None

        self.m3u8 = tempfile.NamedTemporaryFile(suffix=".m3u8")
        self.base = "/".join(self.m3u8.name.split("/")[:-1])
        self.line_next = 0
        self.voice_archive = {}
        self.audio_list = []
        self.latest_spker = ''
        self.ref_text = None
        
        # WAV file parameters
        self.sample_rate = 16000  # 16 kHz
        self.num_channels = 1  # Mono audio
        self.bytes_per_sample = 2  # Assuming 16-bit audio
        
        # Audio Buffer
        self.audio_buffer = b''

        # buffer duration=self.chunk_duration if buffer fulfilled, otherweise it is self.timeout
        if self.mode=="SendRevision":
            self.chunk_duration = 10  # Each chunk is 10 seconds
        else:
            self.chunk_duration = 3

        self.chunk_size = int(self.chunk_duration * self.sample_rate * self.bytes_per_sample)
        self.timer = 0
        self.paragraph_start = "True"
        self.start_time = 0
        self.sent_audio_len = 0
        self.buffer_threshold = 1 # Remaining Time window (in seconds) to expect the next data

        if self.mode=="SendRevision":
            self.wait_time = 2
        else:
            self.wait_time = 0  # Time window (in seconds) to expect the next data

        self.last_data_time = 0.0  # Timestamp of when the last data was received
        # initialise the m3u8 wavheader
        self.init_m3u8()
        # the timer will be only effective when this flag=="True"
        self.prev_stable = "False"
        self.analyse = False

    @property
    def time_next_generation(self):
        return self.have_audio_until - 1/self.realtime_factor

    def send_second_silence(self):
        silence = self.get_silence(1)
        self.send_audio(silence)
        print("SENT BETWEEN SILENCE",1,"for session",self.session,"and tag",self.tag)
            
        self.send_live_video(silence, {"length": 1})
        
    def process(self):
        if not self.running:
            return
        # Try loading speech sample for voice conversion if not using voice_profile for voice_conversion
        # voice profile is activated automatically by including speaker diariasation in the graph
        # if not self.voice_profile:
        #     self.update_audio() 

        try:
            data = self.read_text()
        except:
            data = None
            
        if not data:
            return
        if not "seq" in data or not data["seq"].strip():
            return
            
        # if data is not None and ("unstable" not in data or data["unstable"] is False) and data["sender"].startswith(('mt', 'asr:speakerdiarization')):
        #     print("data: ", data)
        
        # collect original audio for voice profile
        if data is not None and ("unstable" not in data or data["unstable"] is False) and data["sender"].startswith('asr:0') and "audio" in data:
            start, end = float(data["start"]), float(data["end"])
            print({k: v for k, v in data.items() if k !='audio'})
            self.audio_list.append({"start": start, "end": end, "audio": data['audio'], "text": data['seq']})
            
        # setting voice profile
        if data is not None and ("unstable" not in data or data["unstable"] is False) and data["sender"].startswith('asr:speakerdiarization'):
            start, end = float(data["start"]), float(data["end"])
            for audio in self.audio_list:
                if audio['start']==start and audio['end']==end:
                    # print("speaker: ", data['speakerName'])
                    if data['speakerName'] not in self.voice_archive:
                        self.voice_archive[data['speakerName']] = {}
                        self.voice_archive[data['speakerName']]['audio']=audio['audio']
                        self.voice_archive[data['speakerName']]['text']=audio['text']
                    elif len(self.voice_archive[data['speakerName']]["audio"]) < (self.sample_rate*self.bytes_per_sample*self.audio_length_voice_conversion):
                        self.voice_archive[data['speakerName']]['audio']+=audio['audio']
                        self.voice_archive[data['speakerName']]['text']+=audio['text']
                    # Keep only the last 4 items
                    self.audio_list = self.audio_list[-4:]
                    break
         
        # Create TTS
        if data is not None and ("unstable" not in data or data["unstable"] is False) and data["sender"].startswith(('mt', 'textseg:tts', 'bot')):
            print('MT incoming message: ', data)
            start, end, seq = float(data["start"]) if "start" in data else 0, float(data["end"]) if "end" in data else 1, data["seq"]
            # if new sentence already arrive, then no need for active breaking
            if self.prev_stable=="True":
                print("*"*50)
                print("[ABL]: Active Breaker Log: ")
                print("[ABL]: new text coming... pause the active breaker...")
                print("*"*50)
                self.prev_stable = "False"
            
            if seq.strip() != "":
                #self.send_text(start, end, "start running TTS", stable=True)
                start_time = time.time()
                    
                if self.voice_profile:
                    if 'speakerName' in data:
                        self.latest_spker = data['speakerName']
                    if self.latest_spker is not None and self.latest_spker in self.voice_archive:
                        self.audio = self.voice_archive[self.latest_spker]["audio"]
                        self.ref_text = self.voice_archive[self.latest_spker]["text"]
                self.last_timestamp = end
                # Text-to-Speech:
                print("#"*50)
                print(f"[TTS]: {self.language} Text Input for continuity TTS: ", seq)
                # self.send_text(start, end, "start running TTS", stable=True)
                #
                if self.language=="en":
                    if self.last_info:
                        prev_text, prev_dur, prev_pitch, prev_energy = self.last_info["text"], self.last_info["durs"], self.last_info["pitch"], self.last_info["energy"]
                    else:
                        prev_text, prev_dur, prev_pitch, prev_energy = "", None, None, None

                    cur_sen = prev_text +'<\s>'+seg
                    pcm_s16le, durs, f0, energy, prev_text = self.flow_synthesize_speech(bytes(), cur_sen, language=self.language, len_scale=1.0, dr_seg=prev_dur, pitch_seg=prev_pitch, energy_seg=prev_energy)
                    self.last_info["text"], self.last_info["durs"], self.last_info["pitch"], self.last_info["energy"] = prev_text, np.log(1.0+durs), f0, energy
                else:
                    pcm_s16le = self.synthesize_speech(bytes(), seg, language=self.language, len_scale=1.0, ref_text=None)
                    
                middle_time = time.time()
                latency = middle_time - start_time
                seconds = len(pcm_s16le)/(self.sample_rate*self.bytes_per_sample)
                
                # if self.analyse:
                #     self.audio_arrival_time_list.append(middle_time)
                #     self.audio_len_list.append(seconds)
                    
                self.send_live_video(pcm_s16le, {"length": seconds, "tts-start": start, "tts-end": end, "transcript": seq})
                latency2 = time.time() - middle_time
                print("[TTS]: time used for TTS generation: ",f"{latency:.2f} s")
                print("[TTS]: time used for sending: ",f"{latency2:.2f} s")
                if seconds!=0.0:
                    print("[TTS]: Real-time Factor: ", f"{((latency+latency2)/seconds):.2f}")
                # self.send_text(start, end, "finish running TTS", stable=True)
                print("[TTS]: SENT AUDIO",seconds,"for session",self.session,"and tag",self.tag)
                print("#"*50)

                self.last_data_time = time.time()  # Update the timestamp
                print("register self.last_data_time: ", str(self.last_data_time))
            # the active buffer will be activated only when the passive buffer breaker is not activated
            if self.paragraph_start == "True":
                self.prev_stable = "True"

        # taking the previous stable one as reference
        # TODO: how to make sure the asr out is deactivating exactly the timer that we want?
        if data is not None and data["sender"].startswith('asr:speakerdiarization') and data["words"] is not None and self.prev_stable=="True":
            print('[ASR]: incoming message: ', data)
            print(f"[ASR]: {self.language} start_timestamp: ", float(data["start"]))
            print(f"[ASR]: {self.language} last_timestamp: ", self.last_timestamp)
            print(f"[ASR]: {self.language} time_diff: ", float(data["start"]) - self.last_timestamp)
            start_timestamp = float(data["start"])
            time_diff = abs(start_timestamp - self.last_timestamp)
            if time_diff < self.wait_time and time_diff>=0.0:
                # check the timestamp to see if it is active pause
                self.prev_stable = "False"
                print("*"*50)
                print(f"{self.language} asr output deactivate the timer.")
                print("*"*50)

        # Actively Breaking the Buffer
        if self.prev_stable=="True":
            print("*"*50)
            print("[ABL]: Active Breaker Log: ")
            print(f"[ABL]: {self.language} active break timer activated: ", self.prev_stable)
            print(f"[ABL]: {self.language} last stable time: ", str(self.last_data_time))
            print(f"[ABL]: {self.language} current time: ", time.time())
            print("*"*50)
        if (time.time()-self.last_data_time) > self.wait_time and self.prev_stable=="True":
            if len(self.audio_buffer)> 0:
                print("*"*50)
                print(f"[ABL-ac]: {self.language} time:", time.time())
                print(f"[ABL-ac]: {self.language} Actively Breaking the Buffer...")
                print(f"[ABL-ac]: {self.language} send the remaining video immediately...")
                self.send_remaining_video(send_video_additional_dict={})
                # # TODO add silence audio?
                # self.silence_start = 
                #
                print(f"{self.language} Restart buffer process...")
                self.sent_audio_len=0
                self.paragraph_start = "True"
                self.flag_timeout="False"
                self.prev_stable="False"
                print("*"*50)
        time.sleep(0.1)  # Avoid tight looping; check every 100ms
            
        if self.paragraph_start=="False":
            print("%"*50)
            print("[PBL]: Passive Breaker Log: ")
            print(f"[PBL]: {self.language} passive breaker activated: ", "False" if self.paragraph_start=="True" else "True")
            print(f"[PBL]: {self.language} check already sent_audio_len: ", self.sent_audio_len)
            print(f"[PBL]: {self.language} check the start time: ", self.start_time)
            print(f"[PBL]: {self.language} check the current time: ", time.time())
            print(f"[PBL]: {self.language} check how many time left: ", (self.sent_audio_len - (time.time()-self.start_time)))
            print("%"*50)
        # Passively Breaking the Buffer
        if (self.sent_audio_len - (time.time()-self.start_time)) < self.buffer_threshold and self.paragraph_start=="False":
            # deactivate active buffer breaker then
            self.prev_stable = "False"
            # keep the log of pbl action
            if len(self.audio_buffer)> 0:
                self.send_remaining_video(send_video_additional_dict={})
                audio_len = len(self.audio_buffer)/(self.sample_rate * self.bytes_per_sample)
                self.sent_audio_len += audio_len
                print("%"*50)
                print(f"[PBL-ac]: {self.language} Passively Breaking the Buffer...")
                print(f"[PBL-ac]: {self.language} send the remaining video immediately...")
                print(f"[PBL-ac]: {self.language} continue waiting...")
                print("%"*50)
            else:
                print("%"*50)
                print(f"[PBL-ac]: {self.language} Restart buffer process...")
                print("%"*50)
                self.sent_audio_len=0
                self.paragraph_start = "True"
                
    def stop_working(self):
        if not self.running:
            return
        self.running = False
        self.send_end()
            
    def init_m3u8(self):
        # print("self.m3u8.name: ", self.m3u8.name)
        with open(self.m3u8.name, "w") as f:
            f.write("#EXTM3U\n")
            f.write("#EXT-X-VERSION:3\n")
            # print("self.chunk_duration: ", self.chunk_duration)
            f.write(f"#EXT-X-TARGETDURATION:{self.chunk_duration}\n")
            f.write("#EXT-X-MEDIA-SEQUENCE:0\n")
            
    def send_live_video(self, pcm_s16le, send_video_additional_dict):
        self.audio_buffer += pcm_s16le
        print(f"{self.language} adding segments...")
        if len(self.audio_buffer) >= self.chunk_size:
            while len(self.audio_buffer) >= self.chunk_size:
                print(f"{self.language} buffer limit reached...")
                chunk = self.audio_buffer[:self.chunk_size]
                self.audio_buffer = self.audio_buffer[self.chunk_size:]
                send_video_additional_dict['length'] = self.chunk_duration
                # self.send_audio(chunk, additional_dict=send_video_additional_dict)
                # Create a named temporary file for the chunk
                with tempfile.NamedTemporaryFile(suffix=".ts", delete=True) as temp_chunk_file:
                    chunk_filename = temp_chunk_file.name
                    # Convert and write the chunk to the temporary file
                    self.convert_to_ts(chunk, chunk_filename)
                    # Update the playlist
                    self.update_playlist(chunk_filename, self.chunk_duration)
                    # Read the chunk file and send it
                    with open(chunk_filename, "rb") as f:
                        ts_data = f.read()
                    # send .ts data back
                    self.send_video(ts_data, additional_dict=send_video_additional_dict)
                    # send raw_pcm data back
                    self.send_audio(chunk, additional_dict=send_video_additional_dict)
                    
                    self.sent_audio_len += self.chunk_duration
                    # 
                    if self.paragraph_start == "True":
                        self.start_time = time.time()
                        self.paragraph_start = "False"
                        # when passive buffer breaker is working, then deactivate active buffer breaker
                        self.prev_stable = "False"
                        
    def send_remaining_video(self, send_video_additional_dict=None):
        chunk = self.audio_buffer
        audio_len = len(chunk)/(self.sample_rate * self.bytes_per_sample)
        
        send_video_additional_dict['length'] = audio_len
        # self.send_audio(chunk, additional_dict=send_video_additional_dict)
        
        self.audio_buffer = b''
        # Create a named temporary file for the chunk
        with tempfile.NamedTemporaryFile(suffix=".ts", delete=True) as temp_chunk_file:
            chunk_filename = temp_chunk_file.name
            # Convert and write the chunk to the temporary file
            self.convert_to_ts(chunk, chunk_filename)
            # Update the playlist
            self.update_playlist(chunk_filename, self.chunk_duration)
            # Read the chunk file and send it
            with open(chunk_filename, "rb") as f:
                ts_data = f.read()
            # send .ts data back
            self.send_video(ts_data, additional_dict=send_video_additional_dict)
            # send raw_pcm data back
            self.send_audio(chunk, additional_dict=send_video_additional_dict)
            
    def update_playlist(self, new_chunk, chunk_duration):
        # print("playlist name: ", self.m3u8.name)
        with open(self.m3u8.name, "a") as f:
            f.write(f"#EXTINF:{chunk_duration:.3f},\n")
            f.write(f"{new_chunk}\n")
            
    def convert_to_ts(self, audio_data, output_ts_path):
        """
        Converts raw audio data to MPEG-TS format using ffmpeg.

        :param audio_data: The raw audio data (bytes).
        :param output_ts_path: Path where the MPEG-TS file will be written.
        """
        # Write WAV data with a dynamic header to a temporary file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav:
            wav_path = temp_wav.name
            self.write_wav_with_dynamic_header(audio_data, wav_path)
        try:
            # Convert the WAV file to MPEG-TS format using ffmpeg
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "warning",
                "-y",  # Automatically overwrite existing files
                "-i", wav_path, "-c:a", "aac", "-b:a", "96k", "-f", "mpegts", output_ts_path
            ]
            subprocess.run(cmd, check=True)
            print(f"MPEG-TS file written to {output_ts_path}")
        except subprocess.CalledProcessError as e:
            print(f"Error during ffmpeg conversion: {e}")
            raise
        finally:
            # Clean up the temporary WAV file
            if os.path.exists(wav_path):
                os.remove(wav_path)


class OnlineStreamingPlaybackAndTTS(OnlineStreamingTTS):
    def __init__(self, *args, realtime_factor=2, **kwargs):
        super().__init__(*args, realtime_factor=realtime_factor, **kwargs)

        self.current_id = -1
        self.current_sub_id = 0
        self.commands = []
        self.len_audio_to_output = {}
        self.current_audios = {}
        self.current_audio_pointer = 0

        self.seconds_sent = 0

        self.last_output = time.perf_counter()

    def send_live_video(self, pcm_s16le, send_video_additional_dict):
        output_video = self.convert_to_ts(pcm_s16le)

        with open(self.m3u8.name,"r") as f:
            lines = f.readlines()[self.line_next:]

        for line in lines:
            line = line.strip()
            if line == "#EXT-X-ENDLIST":
                break
            if line.startswith("#EXTINF"):
                length = float(line[len("#EXTINF:"):-1])
                send_video_additional_dict['length'] = length
            elif not line.startswith("#"):
                with open(self.base+"/"+line,"rb") as f2:
                    output_video = f2.read()
                self.send_video(output_video, additional_dict=send_video_additional_dict)
            self.line_next += 1

    def convert_to_ts(self, video):
        with tempfile.NamedTemporaryFile("wb") as temp:
            temp.write(b'RIFF\xf0\xb2\x1e\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00LIST\x1a\x00\x00\x00INFOISFT\x0e\x00\x00\x00Lavf58.29.100\x00data\xaa\xb2\x1e\x00')
            temp.write(video)
            temp.flush()
            #cmd = "ffmpeg -hide_banner -loglevel warning -i "+temp.name+" -f hls -hls_time 1 -hls_list_size 0 -hls_flags append_list+split_by_time -hls_playlist_type event "+self.m3u8.name
            cmd = "ffmpeg -hide_banner -loglevel warning -i "+temp.name+" -f hls -hls_time 1 -hls_list_size 0 -hls_flags append_list "+self.m3u8.name
            proc = subprocess.run(cmd.split())


    def process(self):
        text_data = self.read_text()

        if text_data is not None and 'tts_id' in text_data:
            new_id = text_data['tts_id']

            new_command = None
            try:
                new_command = json.loads(text_data["seq"])
            except json.decoder.JSONDecodeError:
                new_command = None

            if new_id > self.current_id and new_command is not None:
                self.current_id = new_id
                self.current_sub_id = 0
                self.commands = [new_command]
                self.len_audio_to_output = {}
                self.current_audios = {}
                self.current_audio_pointer = 0
            elif new_id == self.current_id and new_command is not None:
                if self.commands:
                    self.commands.append(new_command)
                else:
                    self.commands = [new_command]

        if self.commands and self.current_sub_id not in self.len_audio_to_output:
            command = self.commands.pop(0)
            if command[0] == 'TTS':
                pcm_s16le = self.synthesize_speech(self.audio, command[1], self.language, self.len_scale)
                pcm_s16le += bytes(32000 - len(pcm_s16le) % 32000)
                self.current_audios[self.current_sub_id] = pcm_s16le
                self.len_audio_to_output[self.current_sub_id] = len(pcm_s16le)
            else:
                self.len_audio_to_output[self.current_sub_id] = command[1] * 32000

        audio_data = self.read_audio(1, return_start_time=True, add_to_start_time=False)

        if type(audio_data) is tuple:
            pcm_s16le, start_time = audio_data

            if pcm_s16le and start_time is not None:
                audio_id = start_time // 100
                sub_id = start_time % 100
                if audio_id > self.current_id:
                    self.current_id = audio_id
                    self.current_sub_id = 0
                    self.commands = []
                    self.len_audio_to_output = {}
                    self.current_audios = {sub_id: pcm_s16le}
                    self.current_audio_pointer = 0
                elif audio_id == self.current_id:
                    if sub_id in self.current_audios:
                        self.current_audios[sub_id] += pcm_s16le
                    else:
                        self.current_audios = {sub_id: pcm_s16le}

        if self.current_sub_id in self.current_audios and self.current_audio_pointer < len(self.current_audios[self.current_sub_id]):
            output_it_1 = (len(self.current_audios[self.current_sub_id]) == self.len_audio_to_output[self.current_sub_id]
                           if self.current_sub_id in self.len_audio_to_output and self.len_audio_to_output[self.current_sub_id]
                           else False)
            output_it_2 = len(self.current_audios[self.current_sub_id]) - self.current_audio_pointer >= 30 * 32000
            if output_it_1 or output_it_2:
                end = min(self.current_audio_pointer + 32000 * 30, len(self.current_audios[self.current_sub_id]))
                seconds = (end - self.current_audio_pointer) / 32000
                jump_to = self.seconds_sent if self.current_sub_id == 0 and self.current_audio_pointer == 0 else None
                self.send_live_video(self.current_audios[self.current_sub_id][self.current_audio_pointer:end],
                                     {"length": seconds, "jumpTo": (self.current_id, jump_to)})
                self.last_output = time.perf_counter()
                self.seconds_sent += seconds
                self.current_audio_pointer = end

                if self.current_audio_pointer >= len(self.current_audios[self.current_sub_id]):
                    self.current_sub_id += 1
                    self.current_audio_pointer = 0

            self.next_callback = time.time() + 0.05
        else:
            if time.perf_counter() - self.last_output > 30:
                seconds_silence_to_add = 1
                pcm_s16le = self.get_silence(seconds_silence_to_add)
                self.send_live_video(pcm_s16le, {"length": seconds_silence_to_add})
                self.last_output = time.perf_counter()
                self.seconds_sent += seconds_silence_to_add

            self.next_callback = time.time() + 10

class TTSActiveSessionStorage(ActiveSessionStorage):
    def __init__(self, *args, timeout=None, **kwargs):
        super().__init__(*args, use_callbacks=True, **kwargs)
        self.timeout = timeout

    def process(self,body):
        data = json.loads(body)

        session = data["session"]
        tag = data["tag"]
        name = self.name
        key = (name,session,tag)
        print('key: ', key)
        if "controll" in data:
            print('session storage data: ', data)

        if "controll" in data:
            if data["controll"] == "START":
                value = self.get(key)
                if value is not None:
                    return
                self.on_start(key,data)
            elif data["controll"] == "END":
                if data["sender"].startswith("user"):
                    return
                self.delete(key)
            elif data["controll"] == "INFORMATION":
                self.on_information(key)
            elif data["controll"] == "PAUSE":
                if key in self.sessions:
                    self.sessions[key].after_receive_pause()
                else:
                    print(f"WARNING: KEY {key} not found in the self.session dict!")
            else:
                print("WARNING: Found unknown controll request "+str(data["controll"])+", ignoring.")
            return

        if "b64_enc_pcm_s16le" in data:
            if("linkedData" in data):
                data["pcm_s16le"] = base64.b64decode(self.db.get(data["b64_enc_pcm_s16le"]))
            else:
                data["pcm_s16le"] = base64.b64decode(data["b64_enc_pcm_s16le"])
            self.save_audio(key,data)

        """if "json_audio_float" in data:
            if("linkedData" in data):
                audio = base64.b64decode(self.db.get(data["json_audio_float"]))
            else:
                audio = data["json_audio_float"]["audioData"]
            audio = np.array(audio) * math.pow(2, 15)
            data["pcm_s16le"] = audio.astype(np.int16).tobytes()
            self.save_audio(key,data)"""

        if "b64_enc_audio" in data:
            if("linkedData" in data):
                audio = base64.b64decode(self.db.get(data["b64_enc_audio"]))
            else:
                audio = base64.b64decode(data["b64_enc_audio"])

            with tempfile.NamedTemporaryFile() as tmp:
                tmp.write(audio)
                tmp.flush()
                process = subprocess.run(("ffmpeg -hide_banner -loglevel warning -vn -i "+tmp.name+" -map 0:a -ac 1 -f s16le -ar 16000 -c:a pcm_s16le -").split(), stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                if process.returncode != 0:
                    print("ERROR: Could not convert input to raw audio")
                    return
                raw_wav = process.stdout
                data["pcm_s16le"] = raw_wav
            self.save_audio(key,data)

        if "seq" in data:
            # if "unstable" in data and not data["unstable"]:
            self.save_text(key,data)

        return key, data

    def on_start(self, key, data):
        name, session, tag = key
        db.setContext(name,session,tag)

        language = db.getPropertyValues("language")
        len_scale = float(db.getPropertyValues("len_scale"))
        generate_silence = db.getPropertyValues("generate_silence") == "True"
        audio_length_voice_conversion = int(db.getPropertyValues("audio_length_voice_conversion"))
        voice_profile = db.getPropertyValues("voice_profile") == "True"
        mode = db.getPropertyValues("mode")
        
        tts_server = self.get_tts_server()
        tts_server_multi = db.getPropertyValues("tts_server_multi")

        version = db.getPropertyValues("version")
        version = "consecutive"

        if version == "offline":
            value = OfflineStreamingTTS(name, session, tag, tts_server, tts_server_multi, language, len_scale, generate_silence, audio_length_voice_conversion, voice_profile, mode, track_links=True)
        elif version == "consecutive":
            value = ConsecutiveStreamingTTS(name, session, tag, tts_server, tts_server_multi, language, len_scale, generate_silence, audio_length_voice_conversion, voice_profile, mode, track_links=True)
        elif version == "online":
            realtime_factor = int(db.getPropertyValues("realtime_factor"))
            value = OnlineStreamingTTS(name, session, tag, tts_server, tts_server_multi, language, len_scale, generate_silence, audio_length_voice_conversion, voice_profile, mode, realtime_factor=realtime_factor, track_links=True)
        elif version == "online_with_playback":
            realtime_factor = int(db.getPropertyValues("realtime_factor"))
            value = OnlineStreamingPlaybackAndTTS(name, session, tag, tts_server, tts_server_multi, language, len_scale,
                                                  generate_silence, audio_length_voice_conversion, voice_profile, mode,
                                                  realtime_factor=realtime_factor, track_links=True, timeout=12)
        else:
            print("ERROR: Did not start session because version is unknown:", version)
            return

        print("debug key: ", key)
        self.set(key, value)

        data["sender"] = name+":"+tag
        con.publish("mediator", session+"/"+tag, jsons.dumps(data)) # Send START to further components

    def on_end(self, session):
        session.stop_working()
        session.remove_links()

    def on_information(self, key):
        name, session, tag = key
        db.setContext(name,session,tag)

        print("Sending INFORMATION.")

        properties = db.getPropertyValues()
        print("PROPERTIES",properties)

        # Do TTS server version request
        url = self.get_tts_server(version=True)

        try:
            res = httpx.post(url, timeout=self.timeout)
            if res.status_code != 200:
                raise httpx.ConnectError("HTTP return code of tts_server version request is not equal to 200.")
        except httpx.ConnectError:
            print("WARNING: Could not request tts_server version, ignoring.")
            version = "Request failed"
        except httpx.TimeoutException:
            print("WARNING: Could not request tts_server version (timeout), ignoring.")
            version = "Request failed"
        else:
            version = res.text

        properties["tts_server_version"] = version

        name += ":"+tag
        data={'session': session, 'controll': "INFORMATION", 'sender': name, name:properties}
        con.publish("mediator", session, jsons.dumps(data))

    def get_tts_server(self, version=False):
        language = db.getPropertyValues("language")
        try:
            tts_server = requests.get(f"{property_provider_url}/get_server/tts/tts_server_{language}", verify=False).json()["data"]
        except:
            tts_server = db.getPropertyValues("tts_server")
        if version:
            url = "/".join(tts_server.split("/")[:-2])+"/version"
            return url
        return tts_server


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--queue-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--queue-port', help='NATS Server address', type=str, default='5672')
    parser.add_argument('--redis-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--redis-port', help='NATS Server address', type=str, default='6379')
    parser.add_argument('--property_provider_url', type=str, default='http://192.168.0.72:5000')
    args = parser.parse_args()

    name = "tts"
    property_provider_url = args.property_provider_url

    db = get_best_database()(args.redis_server, args.redis_port)
    db.setDefaultProperties(property_to_default)

    with open('config.json', 'r') as f:
        config = json.load(f)
    for k,v in config.items():
        property_to_default[k] = v

    d = {"languages": []}
    for k,v in config.items():
        property_to_default[k] = v
        if k.startswith("tts_server_"):
            language = k[len("tts_server_"):]
            d["languages"].append(language)

    language_mapping = property_to_default["language_mapping"]
    
    con = get_best_connector()(args.queue_server,args.queue_port,db,manual_link_deletion=True)
    con.register(db, name, d)

    sessions = TTSActiveSessionStorage(name, con, db)
    con.consume(name, sessions, True)

