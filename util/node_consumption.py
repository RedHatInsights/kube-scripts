#!/usr/bin/env python3

import traceback
from . import kube_api as kube
from prometheus_client import start_http_server, Gauge
from util.kube_api import Observer


def get_size(size_str):
    if size_str[-1] == "i":
        base = 2
        size_str = size_str[:-1]
    else:
        base = 10

    size_unit = size_str[-1].lower()
    if size_unit == "k":
        exponent = 10 if base == 2 else 3
    elif size_unit == "m":
        exponent = 20 if base == 2 else 6
    elif size_unit == "g":
        exponent = 30 if base == 2 else 9
    else:
        return int(size_str)

    size = int(size_str[:-1])

    return size * (base ** exponent)


def get_core_size(size_str):
    if size_str[-1] == "m":
        return .001 * float(size_str[:-1])
    else:
        return 1.0 * float(size_str)


class RunningPods(Observer):

    metric_prefix = "kube_running_pod_container_resource_"
    labels = ["node", "container", "pod", "namespace"]

    def __init__(self):
        start_http_server(8000)
        self.g_cpu_req = Gauge(self.metric_prefix + "requests_cpu_cores",
                               "", self.labels)
        self.g_mem_req = Gauge(self.metric_prefix + "requests_memory_bytes",
                               "", self.labels)
        self.g_cpu_limit = Gauge(self.metric_prefix + "limits_cpu_cores",
                                 "", self.labels)
        self.g_mem_limit = Gauge(self.metric_prefix + "limits_memory_bytes",
                                 "", self.labels)
        self.gauges = [
            self.g_cpu_req, self.g_mem_req, self.g_cpu_limit, self.g_mem_limit
        ]

    def _observe_pod(self, pod):
        for c in pod.containers:
            labels_map = {
                "pod": pod.name,
                "namespace": pod.namespace,
                "node": pod.node,
                "container": c.name
            }
            labels = [labels_map[l] for l in self.labels]

            if pod.status == "Running":
                resources = c.spec["resources"]
                if "requests" in resources:
                    reqs = resources["requests"]
                    cpu_req = get_core_size(reqs.get("cpu", "0"))
                    mem_req = get_size(reqs.get("memory", "0"))
                    self.g_cpu_req.labels(*labels).set(cpu_req)
                    self.g_mem_req.labels(*labels).set(mem_req)

                if "limits" in resources:
                    limits = resources["limits"]
                    cpu_limit = get_core_size(limits.get("cpu", "0"))
                    mem_limit = get_size(limits.get("memory", "0"))
                    self.g_cpu_limit.labels(*labels).set(cpu_limit)
                    self.g_mem_limit.labels(*labels).set(mem_limit)
            else:
                for metric in self.gauges:
                    try:
                        metric.remove(*labels)
                        print("Successfully removed pod %s:%s" % (
                            pod.name, c.name))
                    except KeyError:
                        pass
                    except:
                        traceback.print_exc()

    def _observe_event(self, event):
        pass

    def observe(self, resource, feed):
        try:
            if type(resource) == kube.Pod:
                self._observe_pod(resource)
            elif type(resource) == kube.Event:
                self._observe_event(resource)
        except Exception as e:
            print("ERROR: %s" % e)
            traceback.print_exc()