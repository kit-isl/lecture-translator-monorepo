#!/usr/bin/env python3
import argparse
import json

import time

from qbmediator.Connection import get_best_connector

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--queue-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--queue-port', help='NATS Server address', type=str, default='9093')
    args = parser.parse_args()
    con = get_best_connector()(args.queue_server,args.queue_port)


    name="test"

    key=0
    index={}
    index[0]=0
    index[1]=0

    for i in range(10):
        d={"id":index[key],"key":key}
        index[key] += 1
        con.publish(name, str(key), json.dumps(d))
        print("Done publish")
    key=1

    for i in range(3):
        d={"id":index[key],"key":key}
        index[key] += 1
        con.publish(name, str(key), json.dumps(d))
    key=0
    #time.sleep(10)
    for i in range(3):
        d={"id":index[key],"key":key}
        index[key] += 1
        con.publish(name, str(key), json.dumps(d))
