#!/usr/bin/env python3

import logging
import os
import click
import arrow
import time

from threading import Thread

from util import kube_api as kube
from util.kube_api import crayons, DATE_FORMAT, Observer
from util.node_consumption import RunningPods
from util.cloudwatch import setup_cw_logging

logging.basicConfig(format="%(message)s", level=logging.INFO)
logger = logging.root
setup_cw_logging(logger)


class Console(Observer):

    def observe(self, resource, feed):
        self.clear_seen_messages()
        msg = repr(resource)
        now = arrow.now()

        if self.has_been_seen(msg, now):
            return

        self.seen_messages[msg] = now

        if resource.last_seen is None or resource.last_seen > self.since:
            logger.info(msg)


class SystemOOM(Observer):

    def observe(self, resource, feed):
        self.clear_seen_messages()
        if type(resource) == kube.Event and resource.reason == "SystemOOM":
            now = arrow.now()

            if self.has_been_seen(resource.node, now) or resource.last_seen < self.since:
                return

            self.seen_messages[resource.node] = now

            logger.info(crayons.white("{:*^80}".format("SYSTEM OOM")))
            logger.info(f"Node: {resource.node}")
            logger.info(f"Killed: {resource.last_seen.format(DATE_FORMAT)}")
            logger.info(crayons.white("*" * 80))


class FailedPodKill(Observer):

    def observe(self, resource, feed):
        self.clear_seen_messages()
        if type(resource) == kube.Event and resource.reason == "FailedKillPod":
            msg_key = resource.namespace + resource.name
            now = arrow.now()

            if self.has_been_seen(msg_key, now) or resource.last_seen < self.since:
                return

            self.seen_messages[msg_key] = now

            logger.info(crayons.white("{:*^80}".format("Failed to kill pod")))
            logger.info(f"Pod: {resource.name}")
            logger.info(f"Killed: {resource.last_seen.format(DATE_FORMAT)}")
            logger.info(resource.message)
            logger.info(crayons.white("*" * 80))


class PodOOM(Observer):

    def observe(self, resource, feed):
        if type(resource) != kube.Pod:
            return

        for c in resource.containers:
            if c.state == "terminated" and c.state_data.get("reason") == "OOMKilled":
                killed = arrow.get(c.state_data.get("finishedAt"))
                if killed > self.since:
                    self.console(resource, c, killed)

    def console(self, p, c, killed):
        logger.info(crayons.white("{:*^80}".format("OOM KILLED")))
        logger.info(f"Pod: {p.name}")
        logger.info(f"Container: {c.name}")
        logger.info(f"Killed: {killed.format(DATE_FORMAT)}")
        logger.info(crayons.white("*" * 80))


@click.command()
@click.option("--token", default=os.path.expanduser("~/token"))
@click.option("--api")
@click.option("-n", "--namespace")
@click.option("--color/--no-color", default=True)
@click.option("--ca-store")
def main(token, api, namespace, color, ca_store):

    if not api:
        logger.info("Please specify valid api hostname using --api")
        return

    crayons.enabled = color

    API = f"https://{api}/api/v1"

    with open(token) as fp:
        token = fp.read().strip()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    observers = (Console(), PodOOM(), SystemOOM(), FailedPodKill(), RunningPods())

    if ca_store is not None and ca_store.lower() == "false":
        ca_store = False

    feed = kube.NodeFeed(API, headers, namespace, observers, ca_store)
    Thread(target=feed.fetch_loop).start()
    time.sleep(2)  # Make sure nodes are populated before everything else

    for cls in (kube.PodFeed, kube.EventFeed):
        feed = cls(API, headers, namespace, observers, ca_store)
        Thread(target=feed.fetch_loop).start()


if __name__ == "__main__":
    main(auto_envvar_prefix="OCLOGS")
