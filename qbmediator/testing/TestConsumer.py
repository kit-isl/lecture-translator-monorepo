#!/usr/bin/env python3
import argparse
from qbmediator.Connection import get_best_connector

import time

class TestConsumer():

    def __init__(self):
        print("Start consumer")

    def process(self,body):
        print("Consume message:",body)


        time.sleep(2)

    def processAggregate(self,body):
        print("Consume messages:",body)


        time.sleep(2)




if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--queue-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--queue-port', help='NATS Server address', type=str, default='9093')
    args = parser.parse_args()
    con = get_best_connector()(args.queue_server,args.queue_port)

    name="test"

    consumer = TestConsumer()

#    con.consume(name, consumer)
    con.consume(name, consumer,True)

