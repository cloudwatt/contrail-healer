import os
import abc
import logging
from six import add_metaclass
try:
    from ConfigParser import ConfigParser
except ImportError:
    from configparser import ConfigParser

import gevent
from gevent.queue import Queue, Empty

from contrail_api_cli.command import Command
from contrail_api_cli.exceptions import CommandError
from contrail_api_cli.utils import printo

from .pool import Pool


logger = logging.getLogger(__name__)
pool = Pool()


class HealerError(gevent.GreenletExit):
    pass


class Operation:
    """Operations to be notified about.
    """
    ALL = ['UPDATE', 'CREATE', 'DELETE']
    """all operations"""
    UPDATE = ['UPDATE']
    """update operation"""
    CREATE = ['CREATE']
    """create operation"""
    DELETE = ['DELETE']
    """delete operation"""


class Timer(object):

    def __init__(self, timeout):
        self._timeout = timeout
        self._g = None
        self.ready = False
        self.start()

    def start(self):
        self._g = pool.spawn_later(self._timeout, self._is_ready)

    def _is_ready(self):
        self.ready = True

    def reset(self):
        self.ready = False
        if self._g is not None:
            gevent.kill(self._g)
        self.start()


@add_metaclass(abc.ABCMeta)
class Healer(Command):
    """Base class for Healers.

    A Healer handles a resource type for one or multiple operations.

    *Resource and operation*

    The operation can be one of Operation.ALL, Operation.CREATE,
    Operation.DELETE, Operation.UPDATE or a combination of theses.

    The resource type must be specified in a `resource` attribute
    of the class. The operation type must be specified in a `on`
    attribute of the class::

        from contrail_healer.healer import Healer, Operation

        class MyHealer(Healer):
            resource = 'virtual-ip'
            on = Operation.CREATE

    *Healer queue and buffer*

    Each Healer has a public input queue and an internal buffer queue.
    Every message is taken from the input queue and put in the buffer
    queue. When the buffer queue is full (default: 10 items) or the
    buffer timeout is over (default: 5s) the buffer is empty and only
    unique elements are checked. For example if the buffer contains
    3 UPDATE notifications on the same resource only one check will
    be made.

    The buffer size and the buffer timeout can be adjusted per healer::

        class MyHealer(Healer):
            buffer_size = 5
            buffer_timeout = 10

    Once the buffer is empty the check method will be run for each
    notification. The check can be delayed with the `check_delay`
    attribute (default: 0s)::

        class MyHealer(Healer):
            check_delay = 1

    *Healer check result*

    The :func:`Healer.check` method must return a tuple where the first
    element is the result of the check and the rest of the elements
    will be used as arguments to the :func:`Healer.fix` method.

    The check result can be `True`, `False` or `None`. If `True` no
    fix is made. If `False` the :func:`Healer.fix` is run. If `None`
    the notification is put back in the queue so that the check will
    be run again on the current notification.

    The number of check retries for a single notification can be controlled
    by the `max_check_retries` attribute (default: 3)::

        class MyHealer(Healer):
            max_check_retries = 5

    Each retry will be delayed by 1s on each iteration.

    *Healer configuration*

    Each Healer can use a configuration file for its own usage. The
    configuration file name can be specified in the `config_file`
    attribute. If defined the configuration will be loaded from
    `/etc/contrail-healer/<config_file>` or
    `~/.config/contrail-healer/<config_file>`. When loaded the
    configuration is available in `self.config` which is a
    :class:`ConfigParser` object.

    """
    buffer_timeout = 5
    """Internal buffer lifetime in seconds"""
    buffer_size = 10
    """Internal buffer size"""
    config_file = None
    """Config file name for the healer, if any"""
    check_delay = 0
    """Check delay in seconds"""
    max_check_retries = 3
    """Max retries for failed checks"""

    def __init__(self, *args):
        super(Healer, self).__init__(*args)
        self.queue = Queue()
        self.started = False
        if self.config_file is not None:
            self.config = ConfigParser()
            reads = self.config.read(['/etc/contrail-healer/%s' % self.config_file,
                                      os.path.expanduser('~/.config/contrail-healer/%s' % self.config_file)])
            if len(reads) == 0:
                raise CommandError('Failed to read configuration')
        else:
            self.config = None

        self._buffer = Queue(maxsize=self.buffer_size)
        self._retries = {}

    def start(self):
        if self.started is False:
            self.started = True
            pool.spawn(self._work)
            pool.spawn(self._receive)
            self.log("started")

    def _receive(self):
        while True:
            work = self.queue.get()
            self.log_debug("got %s on %s" % (work[0], work[1]))
            # put work to do in a buffer to avoid duplicate notifications
            self._buffer.put(work)

    def _work(self):
        timer = Timer(self.buffer_timeout)
        while True:
            if self._buffer.empty():
                self.log_debug("buffer is empty")
                timer.reset()
                gevent.sleep(0.1)
                continue
            elif not self._buffer.full() and timer.ready is False:
                self.log_debug("buffer is not full and timer is not ready")
                gevent.sleep(0.1)
                continue
            else:
                if timer.ready:
                    self.log_debug("timer ready process buffer")
                else:
                    self.log_debug("buffer full process buffer")
                timer.reset()
                self._process_buffer()

    def _process_buffer(self):
        to_process = []
        while True:
            try:
                work = self._buffer.get_nowait()
                if work not in to_process:
                    to_process.append(work)
            except Empty:
                break

        for (oper, r) in to_process:
            self.log_debug("processing %s on %s" % (oper, r))
            pool.spawn_later(self.check_delay, self._heal, oper, r)

    def _retry(self, oper, r):
        if (oper, r) not in self._retries:
            self._retries[(oper, r)] = 1
        else:
            self._retries[(oper, r)] = self._retries[(oper, r)] + 1

        nb_retries = self._retries[(oper, r)]
        if nb_retries <= self.max_check_retries:
            self.log("retrying check on %s in %ss" % (r, nb_retries))
            pool.spawn_later(nb_retries, self._buffer.put, (oper, r))
        else:
            self.log("reach max_check_retries on %s" % r)
            del self._retries[(oper, r)]

    def _heal(self, oper, r):
        result = self.check(oper, r)
        if result[0] is False:
            self.log("%s NOT OK. FIXING!" % r)
            self.fix(*result[1:])
        elif result[0] is None:
            self._retry(oper, r)
        else:
            self.log("%s is OK" % r)

    @property
    def has_json_formatter(self):
        if hasattr(self, '_json_format'):
            return self._json_format
        self._json_format = False
        try:
            import pythonjsonlogger.jsonlogger
        except ImportError:
            pass
        else:
            for handler in logger.handlers:
                if isinstance(handler.formatter, pythonjsonlogger.jsonlogger.JsonFormatter):
                    self._json_format = True
        finally:
            return self._json_format

    @property
    def log_methods(self):
        if hasattr(self, '_log_methods'):
            return self._log_methods
        self._log_methods = {
            'debug': logger.debug,
            'warning': logger.warning,
            'error': logger.error
        }
        if self.has_json_formatter:
            self._log_methods['info'] = logger.info
        else:
            self._log_methods['info'] = printo
        return self._log_methods

    def log(self, message, type='info'):
        if self.has_json_formatter:
            self.log_methods[type](message, extra={'healer': self.__class__.__name__})
        else:
            message = '[%s] %s' % (self.__class__.__name__, message)
            self.log_methods[type](message)

    def log_debug(self, message):
        self.log(message, type='debug')

    def log_warning(self, message):
        self.log(message, type='warning')

    def log_error(self, message):
        self.log(message, type='error')

    def __call__(self):
        # a Healer cannot be called alone for now.
        pass

    @abc.abstractproperty
    def on(self):
        """Operation types the healer cares about.

        :rtype: Operation.TYPE
        """
        return Operation.ALL

    @abc.abstractproperty
    def resource(self):
        """Type of resource the healer work on.

        :rtype: str
        """
        return "resource-type"

    @abc.abstractmethod
    def check(self, oper, resource):
        """The actual check on the resource.

        The method MUST return a tuple where the first element is the check
        result (`True`, `False` or `None`) and the rest of elements are
        arguments for the fix method.

        :rtype: (bool, arg, ...)
        """
        pass

    @abc.abstractmethod
    def fix(self, *args):
        """Fix method is run when the check has returned `False`.
        \*args are provided in the return of the check method.
        """
        pass
