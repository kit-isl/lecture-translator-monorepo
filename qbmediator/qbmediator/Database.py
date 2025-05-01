
import json
import jsons
import time
import redis

class Database:
    def __init__(self):
        """Nothing to do"""
    def storeData(self,workspace,data):
        """Nothing to do"""

    def increaseDataCounter(self,link):
        """Nothing to do"""

    def decreaseDataCount(self,link):
        """Nothing to do"""

    def setContext(self,name,session,stream):
        self.context = name+"/"+session+"/"+stream+"/"
    def getContextString(self,name,session,stream):
        return name+"/"+session+"/"+stream+"/"

    def setDefaultProperties(self,properties):
        self.defaultProperties = properties

    def set(self,key,value):
        """Nothing to do"""
    def get(self,key):
        """Nothing to do"""
    def delete(self,key):
        """Nothing to do"""

    def getPropertyValues(self,key=None,context=[]):
        """Nothing to do"""
    def setPropertyValue(self,key,value,context=[]):
        """Nothing to do"""

class RedisConnection(Database):
    def __init__(self, host,port):
        cR = False
        while (not cR):
            try:
                self.db = redis.Redis(
                    host=host,
                    port=port,
                    password="")
                self.db.ping()
                cR = True
            except Exception as e: 
                print(e)
                print("Wait for redis")
                time.sleep(5)

    def storeData(self,workspace,data):
        print("Store Data:")
        id = self.db.incr(workspace+"linkIDs")
        link = "Data/"+workspace+"/"+str(id)
        self.db.set(link, data);
        print("Store Data:",link)
        return link

    def increaseDataCounter(self,link):
        print("Increase link count:",link)
        self.db.incr("Count/"+link)

    def decreaseDataCount(self,link):
        id = self.db.decr("Count/"+link)
        print("Decrease link count:",link, " to ",id)
        if(id == 0):
            print("Delete data:",link)
            self.db.delete(link)

    def set(self,key,value,context=[]):
        self.db.set(key,value)

    def get(self, key):
        return self.db.get(key)

    def delete(self,key):
        self.db.delete(key)

    def addPropertyKey(self,key,context=[]):
        context = self.getContextString(context[0],context[1],context[2]) if len(context)==3 else self.context

        p = self.db.pipeline()
        p.watch(context+"property_keys")
        p.multi()
        property_keys = self.get(context+"property_keys")
        if property_keys is None:
            property_keys = {}
        else:
            property_keys= json.loads(property_keys)
        property_keys[key] = None # dict because of json.dumps
        p.set(context+"property_keys",json.dumps(property_keys))
        p.execute()

    def getPropertyValues(self,key=None,context=[]):
        context = self.getContextString(context[0],context[1],context[2]) if len(context)==3 else self.context

        if key is not None:
            value = self.get(context+key)
            if value is not None:
                return value.decode("utf-8")
            else:
                if not key in self.defaultProperties:
                    print(f"ERROR: Requesting unknown property {key}, returning None")
                    return None
                return self.defaultProperties[key]
        else:
            res = {}
            for d in [self.defaultProperties, self.get_property_keys(context)]:
                for k, v in d.items():
                    value = self.get(context+k)
                    if value is not None:
                        res[k] = value.decode("utf-8")
                    else:
                        res[k] = v
            return res

    def setPropertyValue(self,key,value,context=[]):
        context_ = self.getContextString(context[0],context[1],context[2]) if len(context)==3 else self.context

        if (value == None):
            self.db.delete(context_+key)
        else:
            self.db.set(context_+key,value)

        self.addPropertyKey(key, context=context)

    def get_property_keys(self, context):
        property_keys = self.get(context+"property_keys")
        if property_keys is None:
            return {}
        else:
            return json.loads(property_keys)


def get_best_database():
    return RedisConnection



def get_from_db(db, name, session, key):
    return db.get(name+key+session)

def set_in_db(db, name, session, key, value, external=False):
    if external: # That the components are able to return all properties during INFORMATION call
        property_keys = get_property_keys(db, name, session)
        property_keys[key] = None # dict because of json.dumps
        set_property_keys(db, name, session, property_keys)

    db.set(name+key+session,value)

def delete_in_db(db, name, session, key):
    db.delete(name+key+session)

def get_property_keys(db, name, session):
    property_keys = get_from_db(db, name, session, "property_keys")
    if property_keys is None:
        property_keys = {}
    else:
        property_keys = json.loads(property_keys)
    return property_keys

def set_property_keys(db, name, session, property_keys):
    set_in_db(db, name, session, "property_keys", json.dumps(property_keys))
def get_used_property(db, property_to_default, name, session, tag, key=None):
    if key is not None:
        value = get_from_db(db, name, session+"/"+tag, key)
        if value is not None:
            return value.decode("utf-8")
        else:
            if not key in property_to_default:
                print(f"ERROR: Requesting unknown property {key}, returning None")
                return None
            return property_to_default[key]
    else:
        res = {}
        for d in [property_to_default, get_property_keys(db, name, session+"/"+tag)]:
            for k,v in d.items():
                value = get_from_db(db, name, session+"/"+tag, k)
                if value is not None:
                    res[k] = value.decode("utf-8")
                else:
                    res[k] = v
        return res

def get_component_names(db):
    names = get_from_db(db, "All", "All", "component_names")
    if names is None:
        names = []
    else:
        names = json.loads(names)
    return names

def add_component_name(db, name):
    names = set(get_component_names(db))
    names.add(name)
    set_in_db(db, "All", "All", "component_names", json.dumps(list(names)))

