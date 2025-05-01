# StreamingASR

To deploy a new model use flask_template.py, start the model somewhere and specify the asr_server in the client-asr-properties.

If you want to use a custom method to search for a stable hypothesis:

1) Subclass StreamingASR (or a subclass of it)
2) Instanciate an instance of you new subclass in SaveAudio (in StreamingASR.py) based on (already present or new) asr-properties.

If you want to use a custom segmenter:
1) Subclass Segmenter (in segmenter.py)
2) Instanciate an instance of you new subclass in the instance of StreamingASR you are using.
