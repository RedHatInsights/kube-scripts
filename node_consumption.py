#!/usr/bin/env python3

import time
from prometheus_client import start_http_server, Gauge
from kubernetes import client, config, watch

LABELS = ["node", "container", "pod", "namespace"]

g_cpu_req = Gauge("kube_running_pod_container_resource_requests_cpu_cores", "", LABELS)
g_mem_req = Gauge("kube_running_pod_container_resource_requests_memory_bytes", "", LABELS)
g_cpu_limit = Gauge("kube_running_pod_container_resource_limits_cpu_cores", "", LABELS)
g_mem_limit = Gauge("kube_running_pod_container_resource_limits_memory_bytes", "", LABELS)


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


def update(v1):
    w = watch.Watch()
    for event in w.stream(v1.list_pod_for_all_namespaces, _request_timeout=60):
        pod = event["object"]
        for c in pod.spec.containers:
            labels_map = {
                "pod": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "node": pod.spec.node_name,
                "container": c.name
            }
            labels = [labels_map[l] for l in LABELS]

            if pod.status.phase == "Running":
                if c.resources.requests:
                    cpu_req = get_core_size(c.resources.requests.get("cpu", "0"))
                    mem_req = get_size(c.resources.requests.get("memory", "0"))
                    g_cpu_req.labels(*labels).set(cpu_req)
                    g_mem_req.labels(*labels).set(mem_req)

                if c.resources.limits:
                    cpu_limit = get_core_size(c.resources.limits.get("cpu", "0"))
                    mem_limit = get_size(c.resources.limits.get("memory", "0"))
                    g_cpu_limit.labels(*labels).set(cpu_limit)
                    g_mem_limit.labels(*labels).set(mem_limit)
            else:
                for metric in (g_cpu_req, g_mem_req, g_cpu_limit, g_mem_limit):
                    try:
                        metric.remove(*labels)
                    except:
                        pass


if __name__ == '__main__':
    start_http_server(8000)
    try:
        config.load_incluster_config()
    except:
        print("WARN: Failed to load cluster config. Assuming running locally.")
        config.load_kube_config()

    v1 = client.CoreV1Api()
    while(True):
        update(v1)
        time.sleep(15)
