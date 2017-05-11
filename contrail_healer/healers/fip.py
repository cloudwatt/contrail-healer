# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from netaddr import IPAddress, IPNetwork

from kazoo.client import KazooClient
from kazoo.handlers.gevent import SequentialGeventHandler

from contrail_api_cli.exceptions import CommandError, ResourceNotFound
from contrail_api_cli.resource import Resource

from ..healer import Healer, Operation


class FIPHealer(Healer):
    """FloatingIP healer.

    This healer makes sure a znode has been correclty created after a
    FIP creation.
    """
    on = Operation.CREATE
    resource = 'floating-ip'
    config_file = 'fip-healer.conf'
    check_delay = 2

    def __init__(self, *args):
        super(FIPHealer, self).__init__(*args)
        zk_server = self.config.get('default', 'zk_server')
        handler = SequentialGeventHandler()
        self.zk_client = KazooClient(hosts=zk_server, timeout=1.0,
                                     handler=handler)
        try:
            self.zk_client.start()
        except handler.timeout_exception:
            raise CommandError("Can't connect to Zookeeper at %s" % zk_server)

        self.vn = Resource('virtual-network',
                           fq_name=self.config.get('default', 'public_vn_fqname'),
                           fetch=True)
        self.subnets = []
        for s in self.vn['network_ipam_refs'][0]['attr']['ipam_subnets']:
            self.subnets.append(IPNetwork('%s/%s' % (s['subnet']['ip_prefix'], s['subnet']['ip_prefix_len'])))

    def _get_subnet_for_ip(self, ip, subnets):
        for subnet in subnets:
            if ip in subnet:
                return subnet

    def _zk_node_for_ip(self, ip, subnet):
        return '/api-server/subnets/%s:%s/%i' % (self.vn.fq_name, subnet, ip)

    def check(self, oper, fip):
        try:
            fip.check()
        except ResourceNotFound:
            return (True,)
        ip = IPAddress(fip.get('floating_ip_address'))
        subnet = self._get_subnet_for_ip(ip, self.subnets)
        if subnet is None:
            self.log_error('No subnet found for FIP %s' % fip)
            return (None,)
        zk_node = self._zk_node_for_ip(ip, subnet)
        return (self.zk_client.exists(zk_node), zk_node, self.vn.uuid)

    def fix(self, zk_node, data):
        return self.zk_client.create(zk_node,
                                     value=str(data),
                                     makepath=True)
