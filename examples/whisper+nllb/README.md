
End-to-End evaluation of whisper+nllb:

1) git clone --recursive https://gitlab.kit.edu/kit/isl-ai4lt/lt-middleware/ltpipeline.git && cd ltpipeline && git submodule update --remote --recursive && cd examples/whisper+nllb
2) bash start_pipeline.sh (you can adjust which GPUs to use in the script; this may take a while; NVIDIA Container Toolkit has to be installed to use the Nvidia GPUs in docker)
3) bash run_evaluation.sh (you can adjust the experiment_name in the script; default is cascaded+offline and it runs create_configs_cascaded+offline.sh)
4) Look at the results on the MLFlow website (how to do that is described in start_pipeline.sh)

