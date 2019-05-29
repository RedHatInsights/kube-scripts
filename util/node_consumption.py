#!/usr/bin/env python3

import arrow
import logging
import traceback
import json
from . import kube_api as kube
from prometheus_client import start_http_server, Gauge
from util.kube_api import Observer

logger = logging.root


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

    pod_metric_prefix = "kube_running_pod_container_resource_"
    node_metric_prefix = "klape_kube_node_status_allocatable_"
    pod_labels = ["node", "container", "pod", "namespace"]
    node_labels = ["node", "type"]

    def __init__(self):
        super().__init__()
        start_http_server(8000)
        self.g_cpu_req = Gauge(self.pod_metric_prefix + "requests_cpu_cores",
                               "", self.pod_labels)
        self.g_mem_req = Gauge(self.pod_metric_prefix + "requests_memory_bytes",
                               "", self.pod_labels)
        self.g_cpu_limit = Gauge(self.pod_metric_prefix + "limits_cpu_cores",
                                 "", self.pod_labels)
        self.g_mem_limit = Gauge(self.pod_metric_prefix + "limits_memory_bytes",
                                 "", self.pod_labels)
        self.pod_gauges = [
            self.g_cpu_req, self.g_mem_req, self.g_cpu_limit, self.g_mem_limit
        ]

        self.g_node_cpu = Gauge(self.node_metric_prefix + "cpu_cores",
                                 "", self.node_labels)
        self.g_node_mem = Gauge(self.node_metric_prefix + "memory_bytes",
                                 "", self.node_labels)
        self.container_map = {}
        self.namespace_map = {}
        self.node_map = {}
        self.nodes = {}

    def _observe_pod(self, pod):
        if pod.node == "???":
            # No node is assigned yet.  It's most likely in a pending state and
            # we'll get another event soon
            return
        self.container_map[pod.name] = [c.name for c in pod.containers]
        self.namespace_map[pod.name] = pod.namespace
        self.node_map[pod.name] = pod.node

        for c in pod.containers:
            labels_map = {
                "pod": pod.name,
                "namespace": pod.namespace,
                "node": pod.node,
                "container": c.name
            }
            labels = [labels_map[l] for l in self.pod_labels]

            node_type = self.nodes[pod.node].type
            if pod.status == "Running" and node_type == "compute":
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
                self._remove_container(labels)

    def _remove_container(self, labels):
        for metric in self.pod_gauges:
            try:
                metric.remove(*labels)
            except KeyError:
                pass
            except:
                traceback.print_exc()

    def _remove_pod(self, name):
        if name in self.container_map:
            for c in self.container_map.get(name):
                labels_map = {
                    "pod": name,
                    "namespace": self.namespace_map[name],
                    "node": self.node_map[name],
                    "container": c
                }
                labels = tuple([labels_map[l] for l in self.pod_labels])
                self._remove_container(labels)
            del self.container_map[name]
            del self.namespace_map[name]
            del self.node_map[name]

    def _observe_event(self, event):
        if event.last_seen < self.since:
            # Don't look at events that happened before the process started
            return
        if event.reason == "Killing" and event.kind == "Pod":
            self._remove_pod(event.obj["name"])
        elif event.reason == "SuccessfulDelete":
            if event.message.startswith("Deleted pod:"):
                pod_name = event.message.split(":")[1].strip()
                self._remove_pod(pod_name)

    def _observe_node(self, node):
        self.nodes[node.name] = node
        labels_map = {
            "node": node.name,
            "type": node.type
        }

        labels = [labels_map[l] for l in self.node_labels]
        if node.ready:
            cpu = get_core_size(node.allocatable["cpu"])
            self.g_node_cpu.labels(*labels).set(cpu)
            mem = get_size(node.allocatable["memory"])
            self.g_node_mem.labels(*labels).set(mem)
        else:
            for metric in (self.g_node_cpu, self.g_node_mem):
                try:
                    metric.remove(*labels)
                except KeyError:
                    pass
                except:
                    traceback.print_exc()

    def observe(self, resource, feed):
        self.clear_seen_messages()
        msg = repr(resource)
        now = arrow.now()

        if self.has_been_seen(msg, now):
            return

        self.seen_messages[msg] = now

        try:
            if type(resource) == kube.Pod:
                self._observe_pod(resource)
            elif type(resource) == kube.Event:
                self._observe_event(resource)
            elif type(resource) == kube.Node:
                self._observe_node(resource)
        except Exception as e:
            print("ERROR: %s" % e)
            traceback.print_exc()
