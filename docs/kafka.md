# Testing 
You can use the Kafka CLI tools installed in the Kafka container for some easy testing and inspection.  
To list the Kafka topics you can run
```
docker compose exec kafka kafka-topics.sh --bootstrap-server localhost:9092 --list
```

You can also list the consumers with
```
docker compose exec kafka kafka-consumer-groups.sh --bootstrap-server localhost:9092 --list
docker compose exec kafka kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group mt
```

To send a message to one of those queues you can try something like
```
docker compose exec kafka kafka-console-producer.sh \
  --bootstrap-server localhost:9092 \
  --property "parse.key=true" --property "key.separator=:" \
  --topic mt
>2:{"start":0,"end":1,"session":"2","tag":"0","sender":"asr:0","seq":"This is the ASR output."}
```


docker compose exec kafka kafka-console-producer.sh --bootstrap-server localhost:9092 --topic mt

# Flushing Pipeline Messages
Sometimes invalid messages get stuck in a loop of crashing the consumer and then being resent to the consumer after it starts back up.
You can flush those messages by setting the message retention to some low value and then waiting for the messages to be deleted.
```
docker compose exec kafka kafka-configs.sh --alter --add-config retention.ms=10000 --bootstrap-server localhost:9092 --topic asr
```
afterwards you can set it back to the default 7 days with
```
docker compose exec kafka kafka-configs.sh --alter --add-config retention.ms=604800000 --bootstrap-server localhost:9092 --topic asr
```
