
from flask import Flask, request
import threading
import queue
import json
import traceback

class FlaskInterface(Flask):
    def __init__(self, inputs, initialize, apply_model, map_request_data=None, route_name="/inference"):
        super().__init__(__name__)

        inititalize_output = initialize()
        if "max_batch_size" in inititalize_output:
            max_batch_size = inititalize_output["max_batch_size"]
        else:
            max_batch_size = 8

        queue_in = queue.PriorityQueue()

        def inference():
            data = request.files
            if not data:
                return "Invalid data", 400

            data_request = {}
            for d in inputs:
                if not "name" in d:
                    print("WARNING: No name in inputs entry dictionary, ignoring!")
                    continue
                name = d["name"]
                if not "type" in d:
                    print("WARNING: No type in inputs entry dictionary, ignoring!")
                    continue
                typ = d["type"]
                
                if not name in data:
                    if "default" in d:
                        value = d["default"]
                    else:
                        return f"ERROR: No mandatory key {name} in data!", 400
                else:
                    value = data[name].read()

                if type(value) == bytes and typ == str:
                    value = value.decode()
                elif type(value) == bytes and typ == int:
                    value = int(value)
                elif type(value) == bytes and typ == float:
                    value = float(value)
                elif type(value) != typ:
                    return f"ERROR: Value of key {name} has to be type {typ} but is type {type(value)}", 400

                data_request[name] = value

            if "priority" not in data_request:
                data_request["priority"] = 0

            if map_request_data is not None: # Can be used e.g. for converting input audio to different format
                data_request = map_request_data(data_request)

            condition = threading.Condition()
            with condition:
                queue_in.put(Priority(condition,data_request))
                condition.wait()

            result = condition.result
            if type(result) != dict:
                print("WARNING: result should be a dictionary, ignoring result!")
                result = {}

            status = 200
            if status in result:
                status = result.pop(status)

            return json.dumps(result), status

        self.add_url_rule(route_name, 'inference', inference, methods=['POST'])

        class Priority:
            next_index = 0

            def __init__(self, condition, data):
                self.index = Priority.next_index

                Priority.next_index += 1

                self.condition = condition
                self.data = data
                self.published = False

            @property
            def priority(self):
                return self.data["priority"]

            def __lt__(self, other):
                return (-self.priority, self.index) < (-other.priority, other.index)

            def get(self, key):
                return self.data.get(key)

            def publish(self, result):
                if self.published:
                    print("WARNING: Tried to publish result more than once!")
                    return
                self.condition.result = result
                try:
                    with self.condition:
                        self.condition.notify()
                except:
                    print("ERROR: Count not publish result")
                self.published = True

        def run_decoding():
            while True:
                reqs = [queue_in.get()]
                while reqs[-1].priority <= 0 and not queue_in.empty() and len(reqs) < max_batch_size:
                    req = queue_in.get()
                    reqs.append(req)

                print("Batch size:",len(reqs),"Queue size:",queue_in.qsize())

                try:
                    apply_model(inititalize_output, reqs)
                    for req in reqs:
                        if not req.published:
                            print("WARNING: apply_model function did not call the publish function on a request, sending error message back!")
                            req.publish({"status":400})
                except Exception as e:
                    print("An error occured during model inference")
                    traceback.print_exc()
                    for req in reqs:
                        req.publish({"status":400})

        decoding = threading.Thread(target=run_decoding)
        decoding.daemon = True
        decoding.start()

    def run(self, port=5008):
        super().run(host="0.0.0.0",port=port,debug=False)

# Example for a worker using this interface:

inputs = [{"name":"pcm_s16le","type":bytes},
          {"name":"input_language","type":str},
          {"name":"output_language","type":str},
          {"name":"prefix","type":str,"default":""},
          {"name":"prev_text_tokens","type":str,"default":""},
          {"name":"priority","type":int,"default":0}, # requests with higher priority are prioritized (requests with priority >= 1 are run with batch size 1)
         ]

def initialize():
    print("Initializing")
    return {"model": "change this to your model", "max_batch_size":4}

def apply_model(inititalize_output, requests):
    model = inititalize_output["model"]

    for req in requests:
        pcm_s16le = req.get("pcm_s16le")
        # use model here
        hypo = "example"
        req.publish({"hypo":hypo})

if __name__ == "__main__":
    app = FlaskInterface(inputs,initialize,apply_model)
    app.run()

    # example call:
    # import requests
    # wav = open("/project/data_asr/common_voice_multilingual/cv-corpus-5.1-2020-06-22/fr/wav/common_voice_fr_17299384.wav","rb").read()[78:]
    # requests.post("http://192.168.0.72:5008/inference", files={"pcm_s16le":wav, "input_language":"fr", "output_language":"fr"})

