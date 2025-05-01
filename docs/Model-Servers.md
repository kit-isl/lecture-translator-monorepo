Aside from the infrastructure (database, message broker, mediator, ...) the LT 2.0 pipeline is currently split into *workers* and *model servers*.
The former handle streams of data in the context of a session, and they may use the latter, which only do pure inference.
Model servers currently all provide a HTTP API for inference.

# Model Servers
Here follows a list of available model servers and how to use/run them.

## NMTGMinor MT Server
Uses [TorchServe](https://pytorch.org/serve/) to serve an NMTGMinor model via HTTP.
You can use an available model for inference via  
```
curl http://i13hpc51.ira.uka.de:8080/predictions/<MODEL NAME> --data "text=This is a test sentence."
```
where `<MODEL NAME>` is the name under which the model was previously registered.

To register a model under the name `<MODEL NAME>` you can run  
```
curl -XPOST "http://i13hpc51.ira.uka.de:8081/models?url=<PATH TO MAR ARCHIVE>&model_name=<MODEL NAME>&batch_size=32&initial_workers=1"
```
Therefore you must have previously exported the NMTGMinor model to a *.mar archive* (which is basically a ZIP-archive with the model, the Python inference handler script, and some extra files) via the [torchserve exporter](https://github.com/pytorch/serve/tree/master/model-archiver#creating-a-model-archive). You may find the required handler for NMTGMinor inference [here](https://github.com/kit-isl/NMTGMinor/blob/torchserve/serving/torchserve/handler.py).  
For more information on the model inference and the model management HTTP API check the [official TorchServe documentation](https://pytorch.org/serve/rest_api.html).

Finally for running the model server you may use  
```
docker run --rm -it -p 8080:8080 -p 8081:8081 -v <PATH TO MODEL STORE>:/home/model-server/model-store:ro registry.isl.iar.kit.edu/torchserve-nmtgminor
```
where the *model store* is the directory containing the models in the form of .mar archives. You may use the store available at  
`/project/iwslt2014c/MT/user/cmullov/models/torchserve/store`
which provides a couple of NMTGMinor models.

For Fairseq inference with TorchServe I will upload the required handler on request.

## Titanic Wav2Lip server, TTS server, SHAS server, NMT server and ASR server
You may find the code for the Titanic project model servers [here](https://git.scc.kit.edu/isl/facedubbing/). 
To start any of the servers
1. download/clone the docker-compose config from [here](https://git.scc.kit.edu/isl/facedubbing/facedubbing)
2. export the apropriate environment variable pointing to your model checkpoint
3. run `docker-compose up <service name>` inside the directory containing the previously cloned `docker-compose.yaml`


# On GPU Support inside Docker Containers
Usually, GPU availability inside of Docker containers is provided by the [NVIDIA container runtime](https://developer.nvidia.com/nvidia-container-runtime), which however doesn't appear to be installed on most of our cluster nodes. For a dirty hack on how to make GPUs available inside of containers you may take a look [here](https://gitlab.kit.edu/carlos.mullov/hacky-cuda-docker).
