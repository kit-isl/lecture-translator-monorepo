
import time
import json
import os
import requests
import base64
import numpy as np
from tqdm import tqdm
import pickle

def parse_line(line):
    delimiter = "▁"
    if not delimiter in line:
        delimiter = "_"
    line = line.split(delimiter)
    time, msg = line[0], json.loads(delimiter.join(line[1:]))
    msg["time_received"] = float(time)
    return msg

class TTSWorker:
    try:
        with open("data.pkl", "rb") as file:
            buffer = pickle.load(file)
    except:
        buffer = {}

    @classmethod
    def get_key(cls, last_text, text, len_scale):
        key = (last_text, text) if len_scale == 1.0 else (last_text, text, len_scale)
        return key

    @classmethod
    def get_from_buffer(cls, last_text, text, len_scale):
        key = cls.get_key(last_text, text, len_scale)
        if key in cls.buffer:
            return cls.buffer[key]

    @classmethod
    def write_to_buffer(cls, last_text, text, len_scale, res):
        key = cls.get_key(last_text, text, len_scale)
        cls.buffer[key] = res

    def __init__(self):
        self.last_text = None
        self.last_info = None

        self.flow_tts_server = "http://192.168.0.62:5058/tts/flow_infer/"
        self.sample_rate = 16000
        self.bytes_per_sample = 2
        
    def infer(self, text, **kwargs):
        len_scale = kwargs.get("len_scale", 1.0)
        res = TTSWorker.get_from_buffer(self.last_text, text, len_scale)
        if not res:
            num_average = 5
            results = []
            for _ in range(5):
                res = self._infer(text, **kwargs)
                results.append(res)
            res = (sum(r[0] for r in results)/len(results),sum(r[1] for r in results)/len(results),res[2])
            TTSWorker.write_to_buffer(self.last_text, text, len_scale, res)
        self.last_text = text
        self.last_info = res[2]
        return res[:2]

    def flow_synthesize_speech(self, audio, text, language="eng", len_scale=1.0, dr_seg=None, pitch_seg=None, energy_seg=None):
        timeout = None
        try:
            # Sending a POST request using the requests library
            response = requests.post(
                    self.flow_tts_server+language,
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

            return audio, dr_pred, pitch_pred, energy_pred

    def _infer(self, text, **kwargs):
        t = time.time()
        # load previous message from last_info 
        last_info = self.last_info
        if last_info:
            prev_text, prev_dur, prev_pitch, prev_energy = last_info["text"], last_info["durs"], last_info["pitch"], last_info["energy"]
        else:
            prev_text, prev_dur, prev_pitch, prev_energy = "", None, None, None
            last_info = {}
        
        # do flow-TTS inference
        cur_sen = prev_text + '<\s>' + text
        pcm_s16le, durs, f0, energy = self.flow_synthesize_speech(bytes(), cur_sen, dr_seg=prev_dur, pitch_seg=prev_pitch, energy_seg=prev_energy, **kwargs)
        infer_time = time.time()-t
        
        # update the last_info
        last_info["text"], last_info["durs"], last_info["pitch"], last_info["energy"] = text, np.log(1.0+durs), f0, energy
        audio_len = len(pcm_s16le)/(self.sample_rate*self.bytes_per_sample)
        
        return audio_len, infer_time, last_info

class TTSWorkerInstant(TTSWorker):
    def infer(self, text, **kwargs):
        res = super().infer(text, **kwargs)
        res = (res[0],0)
        return res

class StreamingImmediately:
    def __init__(self, worker):
        self.worker = worker
        self.reset()

    def reset(self):
        self.remaining_audio_lenghts = []
        self.audio_until = 0

    def send_msg(self, time_send, text, audio_len):
        output = {"time_send": time_send, "text": text, "audio_len": audio_len}
        yield output
        
    def remaining_audio_length(self, timestamp):
        return self.audio_until - timestamp

    def handle_messages(self, msgs, use_tqdm=True):
        for i,msg in enumerate(tqdm(msgs) if use_tqdm else msgs):
            time_received = msg["time_received"]
            text = msg["seq"]
            len_scale = 1.5 if i == 0 else 1.0 # for better comparability to StreamingImmediatelySpeedUp

            audio_len, infer_time = self.worker.infer(text, len_scale=len_scale)

            yield from self.send_msg(time_received+infer_time, text, audio_len)

    def calc_latency(self, worker_outputs, dialog_mode=False):
        if not dialog_mode:
            return self._calc_latency(worker_outputs)
        else:
            worker_outputs = tqdm(worker_outputs)
            res = [self._calc_latency(worker_outputs_, dialog_mode=dialog_mode) for worker_outputs_ in worker_outputs]
            res = {"remaining_audio_lenghts": [l for r in res for l in r["remaining_audio_lenghts"]], "latency": sum(r["latency"] for r in res)/len(res), "latency_start": sum(r["latency_start"] for r in res)/len(res)}
            return res

    def _calc_latency(self, worker_outputs, dialog_mode=False):
        self.reset()

        worker_info = [(time_received, word) for msg in worker_outputs if (time_received:=msg["time_received"]) for word in msg["seq"].split()]

        streaming_info = []
        first_audio_send = None
        for output in self.handle_messages(worker_outputs, use_tqdm=not dialog_mode):
            time_send = output["time_send"]
            if not first_audio_send:
                first_audio_send = time_send
            else:
                self.remaining_audio_lenghts.append(self.audio_until-time_send)
            time_send = max(time_send, self.audio_until)
            words = output["text"].split()
            audio_len = output["audio_len"]
            for i,word in enumerate(words):
                time_send_ = time_send + (i+0.5)/len(words)*audio_len
                streaming_info.append((time_send_,word))
            self.audio_until = time_send + audio_len

        assert len(worker_info) == len(streaming_info)

        res = {"remaining_audio_lenghts": self.remaining_audio_lenghts}
        res["latency"] = sum(t2-t1 for (t1,w),(t2,w) in zip(worker_info,streaming_info))/len(worker_info)
        res["latency_start"] = first_audio_send - worker_outputs[0]["time_received"]
        return res

class StreamingImmediatelySpeedUp(StreamingImmediately):
    def __init__(self, worker, threshold=2, len_scale_slow=1.5, len_scale_fast=1.0):
        super().__init__(worker)
        self.threshold = threshold
        self.len_scale_slow = len_scale_slow
        self.len_scale_fast = len_scale_fast

    def handle_messages(self, msgs, use_tqdm=True):
        len_scale = 1.0
        for i,msg in enumerate(tqdm(msgs) if use_tqdm else msgs):
            time_received = msg["time_received"]
            text = msg["seq"]

            if self.remaining_audio_length(time_received) < self.threshold:
                len_scale = self.len_scale_slow
            else:
                len_scale = self.len_scale_fast

            audio_len, infer_time = self.worker.infer(text, len_scale=len_scale)

            yield from self.send_msg(time_received+infer_time, text, audio_len)

class StreamingImmediatelyWordByWord(StreamingImmediately):
    def handle_messages(self, msgs):
        for msg in tqdm(msgs):
            text = msg["seq"]
            time_received = msg["time_received"]
            for word in text.split():
                audio_len, infer_time = self.worker.infer(word)
                time_received += infer_time
                yield from self.send_msg(time_received, word, audio_len)

class StreamingSmoother(StreamingImmediately):
    def __init__(self, worker):
        super().__init__(worker)
        self.buffer_len = 3
        self.time_out = 2
        self.actual_len = 0
        self.prev_timereceived = None
        self.passive_buffer_breaker = False
        self.audio_len_list = []
        self.infer_time_list = []
        self.prev_infertime = None
        self.sent_msg_list = []
        self.sent_label_list = []
        self.buffer = []
        
    def handle_messages(self, msgs, use_tqdm=True):
        self.mt_received_list = [msg["time_received"] for msg in msgs]
        
        for msg in tqdm(msgs) if use_tqdm else msgs:
            text = msg["seq"]
            audio_len, infer_time = self.worker.infer(text)
            self.audio_len_list.append(audio_len)
            self.infer_time_list.append(infer_time)
            
        for idx, msg in enumerate(tqdm(msgs)) if use_tqdm else enumerate(msgs):
            text = msg["seq"]
            time_received = msg["time_received"]
            next_mt_time_received = self.mt_received_list[idx+1]
            
            audio_len, infer_time = self.worker.infer(text)
            # load audio into buffer
            self.actual_len += audio_len
            self.buffer.append(text)
            
            # test if buffer break:
            if not self.buffer_start and self.actual_len >= self.buffer_len:
                self.buffer_starttime = time_received
                self.buffer_start = True
            
            if self.buffer_start:
                self.buffer_history_len += audio_len
                
            # fulfilled then send
            
            while self.actual_len >= self.buffer_len:
                self.actual_len -= self.buffer_len
            
            # decide if the buffer should be broken
            
            
            
            for text in self.buffer:
                yield from self.send_msg(time_received+infer_time, text, self.buffer_len)
            
            if self.passive_buffer_breaker:
                # passive buffer breaker started
                # TODO
            else:
                # active buffer breaker being activated
                if next_mt_time_received - time_received - infertime > self.timeout:
                    yield from self.send_msg(time_received+infer_time+self.timeout, text, self.actual_len)  
                    self.sent_msg_list.append(idx)
                    self.sent_label_list.append("full")
                    
            if self.actual_len > 0 and self.prev_timereceived is not None:
                # activate the active buffer breaker
                if time_received - self.prev_timereceived > self.timeout:
                    yield from self.send_msg(time_received+infer_time+self.timeout, text, self.actual_len)
                # activate the passive buffer breaker
                else:
                    pass
                    
                
            self.prev_timereceived = time_received
            

    # def handle_messages(self, msgs):
    #     pass # TODO: implement streaming algorithm: Look at msgs and use self.send_msg

def split_input_into_segments(worker_outputs):
    dialog_turn_length_break = 10 # 10 seconds and then until the next point
    last_segment_ends = True
    append = True
    worker_outputs_ = []
    msgs = []
    for msg in worker_outputs:
        if append:
            msgs.append(msg)
        if msgs and float(msgs[-1]["end"]) >= float(msgs[0]["start"]) + dialog_turn_length_break and msgs[-1]["seq"].endswith("."):
            #print("Turn length:",float(msgs[-1]["end"])-float(msgs[0]["start"]))
            worker_outputs_.append(msgs)
            msgs = []
            append = False
        if msg["speech_segment_ends"]:
            append = True
    return worker_outputs_

def measure(lt_output_file, worker_name="mt:0", msg_max=-1, worker_class=TTSWorker, streaming_class=StreamingImmediately, streaming_args={}, dialog_mode=False):
    lt_output = [parse_line(line) for line in open(lt_output_file, "r")]
    worker_outputs = [msg for msg in lt_output if msg["sender"]==worker_name and not "controll" in msg and not msg["unstable"] and msg["seq"].strip()]
    if dialog_mode:
        worker_outputs = split_input_into_segments(worker_outputs)
    if msg_max > -1:
        worker_outputs = worker_outputs[:msg_max]

    worker = worker_class()
    streaming = streaming_class(worker, **streaming_args)

    res = streaming.calc_latency(worker_outputs, dialog_mode=dialog_mode)
    return res

def print_audio_lengths(lengths, mode=0):
    if mode == 0:
        for l in lengths:
            print(f"{l:4.1f}", end=" ")
        print()
    else:
        quantiles = np.quantile(lengths, [0.05,0.95])
        num_buckets = 10
        points = [float("-Inf")]+[x for x in np.linspace(quantiles[0], quantiles[1], num=num_buckets-1)]+[float("Inf")]

        for p1,p2 in zip(points[:-1],points[1:]):
            print(f"        {p1:4.1f} <= x < {p2:4.1f}: {len([l for l in res['remaining_audio_lenghts'] if p1 <= l < p2]):4d}")

if __name__ == "__main__":
    streaming_info = []
    streaming_info.append((StreamingImmediately, {}))
    for threshold in range(5):
        streaming_info.append((StreamingImmediatelySpeedUp, {"threshold": threshold}))

    seconds = 5
    for input_file in ["lt_output_sendPartial.txt"]:
        if not os.path.isfile(input_file):
            continue
        for worker_class in [TTSWorker]: #, TTSWorkerInstant]:
            for streaming_class, streaming_args in streaming_info:
                for worker_name in ["mt:0"]:
                    try:
                        res = measure(input_file, worker_name=worker_name, worker_class=worker_class, streaming_class=streaming_class, streaming_args=streaming_args, dialog_mode=True)
                    except AssertionError:
                        print(f"ERROR in {streaming_class = }, continuing!")
                    ooa = 100*len([l for l in res["remaining_audio_lenghts"] if l<0])/len(res["remaining_audio_lenghts"])
                    tma = 100*len([l for l in res["remaining_audio_lenghts"] if l>seconds])/len(res["remaining_audio_lenghts"])
                    print(f"{input_file = }, {worker_class = }, {streaming_class = }, {streaming_args = }\n    {worker_name = :5s}, latency_start = {res['latency_start']:5.2f}s, latency = {res['latency']:5.2f}s, out of audio: {ooa:.1f}%, more than {seconds} seconds of audio: {tma:.1f}%")
                    #print_audio_lengths(res["remaining_audio_lenghts"], mode=1)

                    with open("data.pkl", "wb") as file:
                        pickle.dump(TTSWorker.buffer, file)

