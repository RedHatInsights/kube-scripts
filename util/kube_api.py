import json
import requests
import time
import random
import arrow
import logging

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


class Project(Resource):

    def __init__(self, d):
        super().__init__(d)
        md = self.metadata
        self.name = md["name"]
        self.status = d["status"].get("phase", "")

    def __eq__(self, o):
        return o is not None and o.name == self.name

    def __repr__(self):
        return "%s is %s" % (self.name, self.status)


class Node(Resource):

    def __init__(self, d):
        super().__init__(d)
        md = self.metadata
        self.name = md["name"]
        self.type = self.extract_type()  # compute, infra, master
        self.taints = d["spec"].get("taints")
        self.started = arrow.get(md["creationTimestamp"])
        self.allocatable = d["status"]["allocatable"]
        self.kernel = d["status"]["nodeInfo"]["kernelVersion"]
        self.kube_version = d["status"]["nodeInfo"]["kubeletVersion"]

    def extract_type(self):
        labels = self.metadata["labels"]
        if "type" in labels:
            return labels["type"]
        for key in labels.keys():
            if key.startswith("node-role.kubernetes.io"):
                return key.split("/")[1]

    @property
    def ready(self):
        for condition in self.data["status"]["conditions"]:
            if condition["type"] == "Ready":
                return condition["status"].lower() == "true"

    def __eq__(self, o):
        return o is not None and o.name == self.name

    def __repr__(self):
        return "%s [%s] %s is %s" % (
            self.started.format(DATE_FORMAT),
            self.type,
            crayons.white(self.name),
            "READY" if self.ready else "NOT READY"
        )


class Pod(Resource):

    def __init__(self, d):
        super().__init__(d)
        md = self.metadata
        self.namespace = md["namespace"]
        self.name = md["name"]
        self.status = d["status"]["phase"]
        self.node = d["spec"].get("nodeName", "???")
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
        self.reason = d.get("reason", "???")
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


class Observer(object):

    def __init__(self, since=arrow.now().shift(minutes=-1)):
        self.since = since
        self.seen_messages = {}

    def has_been_seen(self, msg_key, now):
        return (msg_key in self.seen_messages and  # noqa: W504
                self.seen_messages[msg_key] > self.since)

    def clear_seen_messages(self):
        if random.randint(0, 100) == 1:
            hour_ago = arrow.now().shift(hours=-1)
            for k, v in list(self.seen_messages.items()):
                if v < hour_ago:
                    del self.seen_messages[k]

    def observe(self, resource, feed):
        pass


class OpenshiftFeed(object):

    resource = None
    api_suffix = None

    def __init__(self, api, headers, namespace, observers, ca_store):
        self.api = api
        self.headers = headers
        self.namespace = namespace
        self.observers = observers
        self.ca_store = ca_store
        self.resources = {}

    def fetch_loop(self):
        while True:
            try:
                ns_url = f"namespaces/{self.namespace}/" if self.namespace else ""

                kwargs = {"headers": self.headers, "stream": True}
                if self.ca_store is not None:
                    kwargs["verify"] = self.ca_store

                url = f"{self.api}/watch/{ns_url}{self.api_suffix}"
                print(f"Calling {url}")
                r = requests.get(url, **kwargs)

                if r.status_code != 200:
                    print(f"Invalid status from server: %s\n%s" % (
                        r.status_code,
                        r.json()['message']
                    ))
                    return

                for l in r.iter_lines():
                    d = json.loads(l)
                    resource = self.resource(d["object"])
                    self.resources[resource.name] = resource
                    for o in self.observers:
                        o.observe(resource, self)
            except Exception:
                logging.exception("Failed connection")
            print("Reconnecting...")
            time.sleep(1)


class PodFeed(OpenshiftFeed):

    resource = Pod
    api_suffix = "pods"


class NodeFeed(OpenshiftFeed):

    resource = Node
    api_suffix = "nodes"


class EventFeed(OpenshiftFeed):

    resource = Event
    api_suffix = "events"


class ProjectFeed(OpenshiftFeed):

    resource = Project
    api_suffix = "projects"
