import arrow
import crayons as _crayons


class Crayons:
    def __init__(self):
        self.enabled = True

    def __getattr__(self, name):
        if self.enabled:
            return getattr(_crayons, name)
        else:
            return lambda s: s


crayons = Crayons()
COLOR_KEYS = ('red', 'green', 'blue', 'yellow', 'cyan', 'magenta', 'white', 'black')
COLORS = [getattr(crayons, c) for c in COLOR_KEYS]
DATE_FORMAT = "YYYY-MM-DD HH:mm:ss"


def colorit(name):
    if crayons.enabled:
        return COLORS[sum(map(ord, name)) % len(COLORS)](name)
    else:
        return name


class Resource(object):

    name = None
    namespace = None
    last_seen = None

    def __init__(self, d):
        self.data = d
        self.metadata = d["metadata"]


class Container(Resource):

    def __init__(self, name, d):
        """
        Containers get passed the entire pod JSON and pluck its own data out
        based on the name parameter.
        """
        super().__init__(d)
        self.name = name
        self.pluck_data()
        self.namespace = d["metadata"]["namespace"]
        if self.status:
            self.state = list(self.status["state"].keys())[0]
            self.state_data = self.status["state"][self.state]
        else:
            self.state = self.state_data = None

    def pluck_data(self):
        for c in self.data["spec"]["containers"]:
            if c["name"] == self.name:
                self.spec = c
                break

        if "containerStatuses" in self.data["status"]:
            for c in self.data["status"]["containerStatuses"]:
                if c["name"] == self.name:
                    self.status = c
                    break
        else:
            self.status = None


class Pod(Resource):

    def __init__(self, d):
        super().__init__(d)
        md = self.metadata
        self.namespace = md["namespace"]
        self.name = md["name"]
        self.status = d["status"]["phase"]
        self.started = arrow.get(md["creationTimestamp"])
        self.containers = list(self.populate_containers())

    def populate_containers(self):
        for name in (c["name"] for c in self.data["spec"]["containers"]):
            yield Container(name, self.data)

    def __eq__(self, o):
        return o is not None and \
               o.namespace == self.namespace and \
               o.name == self.name and \
               o.status == self.status

    def __repr__(self):
        return "%s %s: [%s] %s" % (
            self.started.format(DATE_FORMAT),
            colorit(self.namespace),
            self.status,
            crayons.white(self.name)
        )


class Event(Resource):
    """
    count: how many times has this event been seen
    first_seen: when was this event first seen
    kind: type of target resource (e.g. pod)
    namespace: namespace of target resource
    name: name of target resource
    last_seen: when was this event last seen
    message: specific info about the event
    metadata: typical metadata on any kubernetes resource
    reason: event type (e.g. SystemOOM, Created, etc)
    source: node hostname
    type: logging level, e.g. Warning, Normal, etc
    """

    def __init__(self, d):
        super().__init__(d)
        self.count = d.get("count")
        self.first_seen = arrow.get(d["firstTimestamp"])
        self.last_seen = arrow.get(d["lastTimestamp"])
        self.obj = d["involvedObject"]
        self.message = d["message"]
        self.reason = d["reason"]
        self.component = d["source"]["component"]
        self.node = d["source"]["host"] if self.component == "kubelet" else None
        self.namespace = self.obj.get("namespace", "???")
        self.name = self.obj.get("name")
        self.kind = self.obj.get("kind")

    def __repr__(self):
        return "%s %s: [%s] on %s - %s" % (
            self.last_seen.format(DATE_FORMAT),
            colorit(self.namespace),
            self.reason,
            crayons.white(f"{self.kind}/{self.name}"),
            self.message
        )
