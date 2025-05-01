import sys
from flask import Flask, request
import numpy as np
import math
import json

host = sys.argv[1] # localhost
port = sys.argv[2] # 5051

app = Flask(__name__)

model = # TODO

# corresponds to a mt_server "http://$host:$port/mt/infer/en,de" in StreamMT.py
# use None when no input- or output language should be specified
@app.route("/mt/infer/<input_language>,<output_language>", methods=["GET"])
def inference(input_language,output_language):
    text = request.files.get("text") # can be None
    if text is None:
        return "ERROR: You have to send text to translate", 400
    text: str = text.read().decode("utf-8")

    prefix = request.files.get("prefix") # can be None
    if prefix is not None:
        prefix: str = prefix.read().decode("utf-8")

    try:
        result: dict = # TODO: use model, text, prefix, input_language, output_language
    except Exception as e:
        print(e)
        return "An error occured", 400

    # result has to contain a key "hypo" with a string as value (other optional keys are possible)
    return json.dumps(result), 200

# called during automatic evaluation of the pipeline to store worker information
@app.route("/mt/version", methods=["GET"])
def version():
    return #TODO, 200

app.run(debug=True, host=host, port=port, use_reloader=True)
