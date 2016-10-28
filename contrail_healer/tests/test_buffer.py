from __future__ import unicode_literals
import gevent
import unittest

from ..healer import Healer


class TestHealer(Healer):
    on = None
    resource = None
    checks = []

    def check(self, oper, r):
        self.checks.append((oper, r))
        return (True,)

    def fix(self):
        pass


class TestBuffer(unittest.TestCase):

    def test_buffer_full(self):
        th = TestHealer('test')
        th.checks = []
        th.start()
        for _ in range(th.buffer_size):
            th.queue.put(('bar', 'foo'))
        gevent.sleep(1)
        self.assertEqual(len(th.checks), 1)

    def test_buffer_timeout(self):
        th = TestHealer('test')
        th.buffer_timeout = 1
        th.checks = []
        th.start()
        th.queue.put(('bar', 'foo'))
        th.queue.put(('foo', 'bar'))
        gevent.sleep(2)
        self.assertEqual(len(th.checks), 2)

    def test_buffer_empty_timer(self):
        th = TestHealer('test')
        th.buffer_timeout = 2
        th.buffer_size = 2
        th.checks = []
        th.start()
        gevent.sleep(2)
        th.queue.put(('bar', 'foo'))
        gevent.sleep(0.5)
        self.assertEqual(len(th.checks), 0)
        gevent.sleep(2)
        self.assertEqual(len(th.checks), 1)
