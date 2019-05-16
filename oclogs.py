#!/usr/bin/env python3

import logging
import os
import click
import arrow

from threading import Thread

from util import kube_api as kube
from util.kube_api import crayons, DATE_FORMAT, Observer


try:
    from slackclient import SlackClient
except Exception:
    pass

logging.basicConfig()


class Console(Observer):

    def observe(self, resource, feed):
        self.clear_seen_messages()
        msg = repr(resource)
        now = arrow.now()

        if self.has_been_seen(msg, now):
            return

        self.seen_messages[msg] = now

        if resource.last_seen is None or resource.last_seen > self.since:
            print(msg)


class SystemOOM(Observer):

    def observe(self, resource, feed):
        self.clear_seen_messages()
        if type(resource) == kube.Event and resource.reason == "SystemOOM":
            now = arrow.now()

            if self.has_been_seen(resource.node, now) or resource.last_seen < self.since:
                return

            self.seen_messages[resource.node] = now

            print(crayons.white("{:*^80}".format("SYSTEM OOM")))
            print(f"Node: {resource.node}")
            print(f"Killed: {resource.last_seen.format(DATE_FORMAT)}")
            print(crayons.white("*" * 80))
            msg = "\n".join([
                ":rotating_light: *System OOM* :rotating_light:",
                f"Node: {resource.node}",
            ])
            self.slack.send_message(msg)


class FailedPodKill(Observer):

    def observe(self, resource, feed):
        self.clear_seen_messages()
        if type(resource) == kube.Event and resource.reason == "FailedKillPod":
            msg_key = resource.namespace + resource.name
            now = arrow.now()

            if self.has_been_seen(msg_key, now) or resource.last_seen < self.since:
                return

            self.seen_messages[msg_key] = now

            print(crayons.white("{:*^80}".format("Failed to kill pod")))
            print(f"Pod: {resource.name}")
            print(f"Killed: {resource.last_seen.format(DATE_FORMAT)}")
            print(resource.message)
            print(crayons.white("*" * 80))

            msg = "\n".join([
                ":super_saiyan: *Failed to kill pod* :super_saiyan:",
                f"Namespace: {resource.namespace}",
                f"Pod: {resource.name}",
                "```%s```" % " ".join(resource.message.split("\n"))
            ])

            self.slack.send_message(msg)


class PodOOM(Observer):

    def observe(self, resource, feed):
        if type(resource) != kube.Pod:
            return

        for c in resource.containers:
            if c.state == "terminated" and c.state_data.get("reason") == "OOMKilled":
                killed = arrow.get(c.state_data.get("finishedAt"))
                if killed > self.since:
                    self.console(resource, c, killed)
                    self.send_slack(resource, c, killed)

    def send_slack(self, p, c, killed):
        msg = "\n".join([
            ":dead-docker: *POD OOM* :dead-docker:",
            f"Namespace: {p.namespace}",
            f"Pod: {p.name}",
            f"Container: {c.name}"
        ])
        self.slack.send_message(msg)

    def console(self, p, c, killed):
        print(crayons.white("{:*^80}".format("OOM KILLED")))
        print(f"Pod: {p.name}")
        print(f"Container: {c.name}")
        print(f"Killed: {killed.format(DATE_FORMAT)}")
        print(crayons.white("*" * 80))


class Slack(object):

    def __init__(self):
        try:
            token = os.environ["SLACK_TOKEN"]
            self.channel = os.environ["SLACK_CHANNEL"]
            self.client = SlackClient(token)
        except Exception:
            self.client = self.channel = None

    def send_message(self, msg):
        if self.client:
            self.client.api_call("chat.postMessage", channel=self.channel, text=msg)


@click.command()
@click.option("--token", default=os.path.expanduser("~/token"))
@click.option("--api")
@click.option("-n", "--namespace")
@click.option("--color/--no-color", default=True)
@click.option("--ca-store")
def main(token, api, namespace, color, ca_store):

    if not api:
        print("Please specify valid api hostname using --api")
        return

    crayons.enabled = color

    API = f"https://{api}/api/v1"

    with open(token) as fp:
        token = fp.read().strip()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    slack = Slack()

    observers = (Console(slack=slack), PodOOM(slack=slack), SystemOOM(slack=slack), FailedPodKill(slack=slack))

    if ca_store is not None and ca_store.lower() == "false":
        ca_store = False

    for cls in (kube.PodFeed, kube.EventFeed):
        feed = cls(API, headers, namespace, observers, ca_store)
        Thread(target=feed.fetch_loop).start()


if __name__ == "__main__":
    main(auto_envvar_prefix="OCLOGS")
