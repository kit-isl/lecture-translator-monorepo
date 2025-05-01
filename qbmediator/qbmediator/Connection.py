import pika
import threading
import time
import json
import jsons
import os
import sys
from collections.abc import MutableMapping

from kafka import KafkaConsumer,KafkaProducer,KafkaAdminClient
from kafka.partitioner.default import DefaultPartitioner
from kafka.admin.new_partitions import NewPartitions
from kafka.admin import NewTopic
from kafka.errors import CommitFailedError

if __name__ == "Connection":
    from Database import set_in_db, add_component_name
else:
    from qbmediator.Database import set_in_db, add_component_name

theme = os.getenv('QUEUE_SYSTEM')

class Connector:
    def __init__(self):
        """Nothing to do"""

    def publish(self,exchange,key,data):
        print("Needs to be implemented")
        raise NotImplementedError

    def consume(self,exchange,callback):
        print("Needs to be implemented")
        raise NotImplementedError

    # Can be used e.g. to store available languages of component and then be accessed by the lt_api
    def register(self, db, name, d:dict):
        add_component_name(db, name)
        set_in_db(db, name, "All", "component_info", json.dumps(d))

large_data = ["b64_enc_pcm_s16le","b64_enc_audio","json_audio_float","b64_enc_video"]

class PikaWorker():

    def __init__(self,callback,connection):
        self.callback = callback
        self.connection = connection

    def on_message(self,ch, method, properties, body):
        data = json.loads(body)
        if not any(key in data for key in large_data): # Prevent to much spam in the docker log
            print(" [x] Received for translation %r " % (body))
        e = threading.Event()
        x = threading.Thread(target=self.heartbeat,args=(e,))
        x.start()
        self.callback.process(body)
        e.set()
        x.join()
        ch.basic_ack(delivery_tag=method.delivery_tag)

    def heartbeat(self,event):
        while not event.is_set():
            self.connection.process_data_events()
            event.wait(3)

class PikaConnector(Connector):
    """Connection to RabbitMQ"""

    def __init__(self,server,port=5672,db=None):
        self.server = server
        self.port = port

    def publish(self,exchange,key,data):
        connection = pika.BlockingConnection(pika.ConnectionParameters(host=self.server,port=self.port))
        channel = connection.channel()
        channel.basic_publish(exchange=exchange, routing_key=key, body=data)
        channel.close()
    def consume(self,exchange,callback):
        connection = None
        while True:
            try:
                connection = pika.BlockingConnection(pika.ConnectionParameters(host=self.server,port=self.port))
                channel = connection.channel()
                channel.exchange_declare(exchange=exchange, exchange_type="x-consistent-hash", durable=True)
                # q = channel.queue_declare(queue="",exclusive=True,auto_delete=True,durable=True)
                args = {}
                args["x-single-active-consumer"] = True
                q = channel.queue_declare(queue=exchange, durable=True, arguments=args)
                # Routing_key: Relative amount of messages send to this queue
                # channel.queue_bind(exchange=exchange, queue=q.method.queue, routing_key="1")
                channel.queue_bind(exchange=exchange, queue=exchange, routing_key="1")
                worker = PikaWorker(callback, connection)
                channel.basic_consume(queue=q.method.queue, on_message_callback=worker.on_message)
                print(' [*] Waiting for messages for ' + exchange)
                channel.start_consuming()
            except BaseException as e:
                print('{!r}; restarting thread'.format(e))
                time.sleep(3)
            else:
                print('exited normally, bad thread; restarting')

class ExpiringDict(MutableMapping):
    def __init__(self, *args, **kwargs):
        self._data = {}
        self.timestamps = {}
        self.last_cleanup = time.time()
        self.update(*args, **kwargs)

    def __setitem__(self, key, value):
        current_time = time.time()
        self._data[key] = value
        self.timestamps[key] = current_time
        self._maybe_cleanup()

    def __getitem__(self, key):
        self._maybe_cleanup()
        if key not in self._data:
            raise KeyError(key)
        current_time = time.time()
        if current_time - self.timestamps[key] > 86400:  # 24 hours
            del self._data[key]
            del self.timestamps[key]
            raise KeyError(key)
        return self._data[key]

    def __delitem__(self, key):
        del self._data[key]
        del self.timestamps[key]

    def __iter__(self):
        self._maybe_cleanup()
        for key in list(self._data.keys()):  # Use list to avoid mutation during iteration
            if key in self.timestamps:
                if time.time() - self.timestamps[key] > 86400:
                    del self._data[key]
                    del self.timestamps[key]
                else:
                    yield key

    def __len__(self):
        self._maybe_cleanup()
        current_time = time.time()
        expired = [key for key in self._data if current_time - self.timestamps[key] > 86400]
        for key in expired:
            del self._data[key]
            del self.timestamps[key]
        return len(self._data)

    def __contains__(self, key):
        self._maybe_cleanup()
        if key not in self._data:
            return False
        current_time = time.time()
        if current_time - self.timestamps[key] > 86400:
            del self._data[key]
            del self.timestamps[key]
            return False
        return True

    def _maybe_cleanup(self):
        current_time = time.time()
        if current_time - self.last_cleanup >= 3600:  # 1 hour
            self._cleanup()
            self.last_cleanup = current_time

    def _cleanup(self):
        current_time = time.time()
        expired_keys = [key for key, ts in self.timestamps.items() if current_time - ts > 86400]
        for key in expired_keys:
            del self._data[key]
            del self.timestamps[key]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def pop(self, key, default=None):
        self._maybe_cleanup()
        if key not in self._data:
            return default
        current_time = time.time()
        if current_time - self.timestamps[key] > 86400:
            del self._data[key]
            del self.timestamps[key]
            return default
        value = self._data.pop(key)
        del self.timestamps[key]
        return value

    def __repr__(self):
        return f"ExpiringDict({self._data})"

class CustomPartitioner(DefaultPartitioner):
    routing = ExpiringDict()
    index = {}
    db = None

    def __init__(self, db):
        super().__init__()
        CustomPartitioner.db = db

    @classmethod
    def __call__(cls, key, all_partitions, available):
        key_ = key.decode().split("/")
        if len(key_) != 3:
            return super().__call__(key, all_partitions, available)
        if key in cls.routing:
            p = cls.routing[key]
            if p in available:
                return p
        session, receiver, tag = key_

        suffix = "_online"
        offset = 0
        if not cls.db:
            version = "online"
        else:
            version = cls.db.getPropertyValues("version", context=[receiver,session,tag])
            if not version:
                version = "online"
        if version == "offline":
            suffix = "_offline"
            offset = len(all_partitions)//2

        index = cls.index.get(receiver+suffix, 0)
        for _ in range(len(all_partitions)//2):
            p = all_partitions[offset+index]
            index = (index + 1) % (len(all_partitions)//2)
            if p in available:
                cls.index[receiver+suffix] = index
                break
        else:
            p = super().__call__(key, all_partitions, available)

        cls.routing[key] = p
        print(f"Routing {key = } with {version = } to partition {p}")
        return p

class KafkaConnector(Connector):
    """Connection to Kafka"""

    def __init__(self, server, port=9093, db=None, manual_link_deletion=False, partitions=10):
        self.server = server
        self.port = str(port)
        self.admin_client = KafkaAdminClient(bootstrap_servers=[self.server+":"+self.port])
        self.producer = KafkaProducer(
            bootstrap_servers=[self.server+':'+self.port],
            value_serializer=str.encode,
            key_serializer=str.encode,
            partitioner=CustomPartitioner(db),
        )
        list = self.admin_client.list_topics()
        print("Topics:", list)
        self.partitions = partitions
        self.linkLargeData = True
        self.maxDataSize = 100000
        self.db = db
        self.manual_link_deletion = manual_link_deletion

        self.consumer = None

    def getSize(self,data):
        return sys.getsizeof(data)

    def createDataPointers(self,body,force_linking=False):
        link = None
        data = json.loads(body)
        for tag in large_data:
            if tag in data:
                if ("linkedData" not in data or data["linkedData"] == False) and (force_linking or self.getSize(data[tag]) > self.maxDataSize):
                    #print("Size:",self.getSize(data[tag]))
                    link = self.db.storeData(data["session"],data[tag])
                    data[tag] = link
                    if self.manual_link_deletion:
                        self.db.increaseDataCounter(data[tag]) # Increase data counter once more to not remove the video
                    data["linkedData"] = True;
                    body=jsons.dumps(data)
                    #print("New message:",body)
                #print ("Linked data:",data)
                if ("linkedData" in data and data["linkedData"] == True):
                    self.db.increaseDataCounter(data[tag])
        return body, link

    def unlinkData(self,body):
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            print(f"Invalid JSON, didn't unlink: {body}")
            return
        if ("linkedData" in data and data["linkedData"] == True):
            for tag in large_data:
                if tag in data:
                    self.db.decreaseDataCount(data[tag])

    def publish(self,exchange,key,data,force_linking=False):
        link = None
        if(self.linkLargeData):
            data, link = self.createDataPointers(data,force_linking=force_linking)
        print("Send to:",exchange," with key:",key)
        self.producer.send(exchange, key=key,value=data)
        self.producer.flush()
        return link

    def consume(self,exchange,callback,aggregate=False,only_one_poll=False,blocking=True):
        if self.consumer is None:
            print("Consume:",exchange)
            self.consumer = KafkaConsumer(exchange,
                bootstrap_servers=[self.server+':'+self.port],
                auto_offset_reset='earliest',
                enable_auto_commit=False,
                group_id=exchange,
            )

            if self.consumer.partitions_for_topic(exchange) is None or len(self.consumer.partitions_for_topic(exchange)) != self.partitions:
                self.consumer.close()
                client = KafkaAdminClient(bootstrap_servers=self.server+':'+self.port)

                if self.consumer.partitions_for_topic(exchange) is None:
                    topic_list = []
                    topic_list.append(NewTopic(name=exchange, num_partitions=self.partitions, replication_factor=1))
                    client.create_topics(new_topics=topic_list, validate_only=False)
                else:
                    rsp = client.create_partitions({
                        exchange: NewPartitions(self.partitions)
                    })
                    print("Create new partitions")
                self.consumer = KafkaConsumer(
                    exchange,
                    bootstrap_servers=[self.server + ':'+self.port],
                    auto_offset_reset='earliest',
                    enable_auto_commit=False,
                    group_id=exchange,
                )

            print("Consume:"+exchange)

        if (aggregate):
            #print("Getting messages in aggregate:")
            max_fetch_messages = 100
            while(True):
                #print("Poll")
                msgList = self.consumer.poll(1000, max_fetch_messages)
                #print("Got messages:",len(msgList))
                try:
                    messages = {}

                    for topic_data, mList in msgList.items():
                        for m in mList:
                            try:
                                k = m.key.decode("utf-8")
                            except Exception as e:
                                print("Received message with invalid key: ", m.key, file=sys.stderr)
                                continue
                            if k in messages:
                                messages[k].append(m.value.decode("utf-8"))
                            else:
                                messages[k] = [m.value.decode("utf-8")]
                    for k in messages.keys():
                        callback.processAggregate(messages[k])

                    self.consumer.commit()

                    max_fetch_messages = min(100, int(max_fetch_messages * 1.15 + 1))
                except CommitFailedError:
                    max_fetch_messages = max_fetch_messages // 2 + 1
                    print(f"Warning: CommitFailedError. Reducing max_fetch_messages to {max_fetch_messages}")

                for k in messages.keys():
                    if (self.linkLargeData == True):
                        for m in messages[k]:
                            self.unlinkData(m)

                if only_one_poll:
                    return len(msgList)

        else:
            if blocking:
                print("Getting messages:")
                for msg in self.consumer:
                    #print("msg",msg)
                    callback.process(msg.value.decode("utf-8"))
                    self.consumer.commit()
            else:
                while True:
                    #print("Getting messages:")
                    msgList = self.consumer.poll(100)
                    for topic_data, mList in msgList.items():
                        for msg in mList:
                            callback.process(msg.value.decode("utf-8"))
                            self.unlinkData(msg.value.decode("utf-8"))

                    self.consumer.commit()

                    if only_one_poll:
                        return len(msgList)

# to change to another connector everywhere
def get_best_connector():
    if theme == "KAFKA":
        return KafkaConnector
    else:
        return PikaConnector
