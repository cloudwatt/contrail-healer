import os
import socket
import logging

from kombu import Connection, Exchange, Queue, Consumer

import gevent

from contrail_api_cli.manager import CommandManager
from contrail_api_cli.command import Command, Option
from contrail_api_cli.resource import Resource
from contrail_api_cli.exceptions import CommandError
from contrail_api_cli.utils import printo

from pool import Pool

VNC_EXCHANGE = 'vnc_config.object-update'
logger = logging.getLogger(__name__)
pool = Pool()


class ConnectionLost(Exception):
    pass


class Heal(Command):
    """contrail-api-cli heal command.

    Plug into the contrail-api RabbitMQ exchange and send notifications
    to registered healers.

    Healers are discovered through the `contrail_api_cli.healer` entrypoint.

    Healers must implement :class:`contrail_healer.healer.Healer` class.

    Usage::

        contrail-api-cli heal --rabbit-url user:pass@server:port --rabbit-vhost opencontrail

    The url and vhost can also be sourced from the environment variables
    `$CONTRAIL_HEALER_RABBIT_URL` and `$CONTRAIL_HEALER_RABBIT_VHOST`.
    """
    rabbit_url = Option(default=os.environ.get('CONTRAIL_HEALER_RABBIT_URL'))
    rabbit_vhost = Option(default=os.environ.get('CONTRAIL_HEALER_RABBIT_VHOST'))

    def __call__(self, rabbit_url=None, rabbit_vhost=None):
        self.rabbit_url = rabbit_url
        self.rabbit_vhost = rabbit_vhost
        self._healers = {}

        self._register_healers()
        self._start_healers()
        self._setup()
        self._start()

    def _setup(self):
        self.conn = Connection("amqp://%s/%s" % (self.rabbit_url, self.rabbit_vhost))
        try:
            self.conn.connect()
        except (socket.timeout, socket.error):
            raise CommandError("Failed to connect to RabbitMQ server")

        exchange = Exchange(VNC_EXCHANGE, 'fanout', durable=False)(self.conn)

        self.queue = Queue("contrail-healer", exchange, durable=False)(self.conn.channel())
        self.queue.declare()

        self.consumer = Consumer(self.conn.channel(),
                                 queues=[self.queue],
                                 callbacks=[self._process])

    def _start(self):
        self.consumer.consume()
        try:
            while True:
                self.conn.drain_events()
                gevent.sleep(0.5)
        except IOError:
            printo("Disconnected from RabbitMQ server, reconnecting")
            self._setup()
            self._start()
        except KeyboardInterrupt:
            self._cleanup()
            raise

    def _cleanup(self):
        logger.debug("Doing some cleanup...")
        self.consumer.cancel()
        pool.kill()

    def _process(self, body, message):
        try:
            resource = body['type']
            oper = body['oper']
        except KeyError:
            pass
        else:
            healers = self._healers.get(resource, {}).get(oper, [])
            if healers:
                pool.spawn(self._broadcast, healers, body)
        finally:
            message.ack()

    def _broadcast(self, healers, body):
        """Send notification to concerned healers.
        """
        if 'obj_dict' in body:
            resource = Resource(body['type'], **body['obj_dict'])
        else:
            if body.get('uuid') is None:
                return
            resource = Resource(body['type'], uuid=body['uuid'])
        for h in healers:
            h.queue.put((body['oper'], resource))

    def _register_healers(self):
        ns = 'contrail_api_cli.healer'
        manager = CommandManager()
        manager.load_namespace(ns)
        for mgr in manager.mgrs:
            if mgr.namespace == ns:
                for ext in mgr.extensions:
                    if ext.obj is not None:
                        self._register_healer(ext.obj)

    def _register_healer(self, healer):
        if healer.resource not in self._healers:
            self._healers[healer.resource] = {}
        for oper in healer.on:
            if oper not in self._healers[healer.resource]:
                self._healers[healer.resource][oper] = []
            self._healers[healer.resource][oper].append(healer)

    def _start_healers(self):
        for resource_type, opers in self._healers.items():
            for oper, healers in opers.items():
                for healer in healers:
                    printo("Starting healer %s" % healer)
                    pool.spawn(healer.start)
