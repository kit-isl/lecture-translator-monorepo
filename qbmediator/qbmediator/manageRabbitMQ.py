#!/usr/bin/env python3
import argparse
from pyrabbit.api import Client

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--rabbitmq-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--exchange', help='Exchange to edit', type=str, default='test')
    args = parser.parse_args()


    cl = Client(args.rabbitmq_server+':15672', 'guest', 'guest')
    queues = [q['name'] for q in cl.get_queues()]
    #queues = [q for q in cl.get_queues()]
    print(queues)
    for q in queues:
        print(q,cl.get_queue_bindings("/",q))
    print(cl.get_exchange("/",args.exchange))