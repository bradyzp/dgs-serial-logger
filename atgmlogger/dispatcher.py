# -*- coding: utf-8 -*-
# This file is part of ATGMLogger https://github.com/bradyzp/atgmlogger

import queue
import logging
import threading
from typing import Dict, Type
from weakref import WeakSet

from .runconfig import rcParams
from .logger import DataLogger
from .plugins import PluginInterface, PluginDaemon
from .types import Command, CommandSignals, DataLine

LOG = logging.getLogger(__name__)
POLL_INTV = 1


class Dispatcher(threading.Thread):
    """The Dispatcher class dispatches messages to the various
    subscribers/plugins.

    Regular plugins are instantiated once, and then passed messages depending on
     their listening criteria.
    Daemon plugins are instantiated each time their conditional evaultes to true

    When a plugin is instantiated, stored parameters (from the global
    configuration) are supplied to them.
    """
    _plugins = set()  # Registered Regular Plugins
    _daemons = set()  # Registered Daemon Plugins
    _params = {}
    _runlock = threading.Lock()

    def __init__(self, collector=None, sig_exit=None):
        super().__init__(name=self.__class__.__name__)
        self.sig_exit = sig_exit or threading.Event()
        self._queue = collector or queue.Queue()
        self._threads = set()
        self._active_daemons = WeakSet()
        self._context = AppContext(self.message_queue)

    @classmethod
    def acquire_lock(cls, blocking=True):
        return cls._runlock.acquire(blocking=blocking)

    @classmethod
    def release_lock(cls):
        cls._runlock.release()

    @classmethod
    def register(cls, klass, **params):
        cls.acquire_lock()
        assert klass is not None
        if issubclass(klass, PluginInterface) and klass not in cls._plugins:
            LOG.debug("Registering class {} in dispatcher as regular"
                      "plugin.".format(klass))
            cls._plugins.add(klass)
            cls._params[klass] = params
        elif issubclass(klass, PluginDaemon) and klass not in cls._daemons:
            LOG.debug("Registering class {} in dispatcher as Daemon"
                      .format(klass))
            cls._daemons.add(klass)
            try:
                klass.configure(**params)
            except (AttributeError, TypeError):
                LOG.warning("Unable to configure daemon class: ", klass)
            cls._params[klass] = params
        else:
            LOG.info("Class %s is already registered in dispatcher.",
                     str(klass))
        cls.release_lock()
        return klass

    @property
    def message_queue(self):
        return self._queue

    def put(self, item):
        self._queue.put_nowait(item)

    def run(self):
        self.acquire_lock(blocking=True)
        LOG.debug("Dispatcher run acquired runlock")

        # Instantiate logger thread
        logger = DataLogger(rcParams['logging.logdir'], self._context)
        logger.start()
        self._threads.add(logger)

        # Create plugin threads
        plugin_type_map = {}
        for plugin in self._plugins:
            try:
                instance = plugin()
                instance.set_context(self._context)
                instance.configure(**self._params[plugin])
            except (TypeError, ValueError, AttributeError, RuntimeError):
                LOG.exception("Error instantiating listener.")
                continue
            else:
                ctypes = instance.consumer_type()
                for ctype in ctypes:
                    consumer_set = plugin_type_map.setdefault(ctype, WeakSet())
                    consumer_set.add(instance)

                instance.start()
                self._threads.add(instance)

        live_daemons = {}  # type: Dict[Type[PluginDaemon], PluginDaemon]
        while not self.sig_exit.is_set():
            try:
                item = self._queue.get(block=True, timeout=POLL_INTV)
            except queue.Empty:
                item = None
            else:
                logger.log(item)
                for subscriber in plugin_type_map.get(type(item), set()):
                    subscriber.put(item)
                self._queue.task_done()

            # Check if a daemon needs to be spawned
            for daemon in self._daemons:
                if daemon not in live_daemons and daemon.condition(item):
                    try:
                        inst = daemon(context=self._context, data=item)
                        inst.start()
                        live_daemons[daemon] = inst
                    except TypeError:
                        LOG.exception("Type error when instantiating "
                                      "daemon: %s", str(daemon))
            # Prune finished daemon threads from the dict
            live_daemons = {k: v for k, v in live_daemons.items()
                            if v.is_alive()}

        self.release_lock()

    def _exit_threads(self, join=False):
        for thread in self._threads:
            thread.exit(join=join)
        for daemon in self._active_daemons:
            daemon.exit(join=join)

    def exit(self, join=False):
        self.sig_exit.set()
        if self.is_alive():
            # We must check if we're still alive to see if it's necessary to
            # put a None object on the queue, else if we join the queue it
            # may block indefinitely
            self._queue.put(None)
        self._exit_threads(join=join)
        if join:
            self.join()

    def signal(self, signal=CommandSignals.SIGHUP):
        """Notify threads of a system signal or user defined event."""
        self.put(Command(signal))


class Blink:
    def __init__(self, led, priority=5, frequency=0.1, continuous=False):
        self.led = led
        self.priority = priority
        self.frequency = frequency
        self.duration = 0
        self.until_stopped = continuous

    def __lt__(self, other):
        return self.priority < other.priority


class AppContext:
    def __init__(self, listener_queue):
        self._queue = listener_queue

    def blink(self, led='data', freq=0.04):
        cmd = Command(Blink(led=led, frequency=freq))
        self._queue.put_nowait(cmd)

    def blink_until(self, until: threading.Event = None, led='usb', freq=0.03):
        # TODO: Possibly allow caller to pass event that the caller can set
        # to end the blink
        cmd = Command(Blink(led=led, frequency=freq, continuous=True))
        self._queue.put_nowait(cmd)

    def log_rotate(self):
        cmd = Command(CommandSignals.SIGHUP)
        self._queue.put_nowait(cmd)
