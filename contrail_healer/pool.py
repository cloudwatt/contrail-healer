"""Module used internally for tracking running greenlets.
"""
from __future__ import unicode_literals
from six import add_metaclass

import gevent.pool

from contrail_api_cli.utils import Singleton


@add_metaclass(Singleton)
class Pool(object):

    def __init__(self):
        self._pool = gevent.pool.Group()

    def kill(self):
        self._pool.kill()

    def spawn(self, func, *args, **kwargs):
        return self._pool.apply_async(func, args=args, kwds=kwargs)

    def spawn_later(self, time, func, *args):
        g = gevent.spawn_later(float(time), func, *args)
        self._pool.add(g)
        return g
