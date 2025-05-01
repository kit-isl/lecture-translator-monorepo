from datetime import date

class Session:

    def __init__(self, id):
        self.id = id
        self.streams = []
        self.graph = None
        self.startDate = date.today().strftime("%Y/%m/%d/")
        self.name = ""
        self.type = ""
        self.password = ""

    def add_stream(self):
        id = len(self.streams)
        self.streams.append({})
        return id;

    def get_start_date(self):
        return self.startDate;

    def set_graph(self,g):
        self.graph = g

    def get_graph(self):
        return self.graph

    def get_receivers(self,node):
        if node not in self.graph:
            print("WARNING: Could not find any receiver in graph.")
            return []
        return self.graph[node]

