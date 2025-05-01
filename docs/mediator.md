# Mediator
The Mediator is the component of the pipeline that connects the different streaming components of the pipeline.
Streaming components (LT-API, streamingasr, streamingmt, ...) therefore maintain a connection to the mediator (through a messaging system like Apache Kafka or RabbitMQ) and send produced data to the mediator.
The mediator reads and interprets the session graph and takes care of relaying data streams to the next components, as specified in the graph.
