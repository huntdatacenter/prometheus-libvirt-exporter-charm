"""
Task Scheduler
==============

Threadsafe lightweight scheduler that features
(delayed) periodic callbacks.

Example: every hour beginning with the next one run
function `basic_fn`.

.. sourcecode:: python

    from scheduler import Scheduler

    scheduler = Scheduler()
    scheduler.add(basic_fn, 'hour')
    scheduler.add(print, 'second', round=2, args=('heartbeat',))
    scheduler.run_concurrent()

Heartbeat print is run every 2 seconds.
"""
import asyncio
import concurrent.futures
import functools
import signal
import sys
import traceback
import uuid
from datetime import datetime
from datetime import timedelta


class Scheduler:
    """
    Task scheduler.

    Threadsafe lightweight scheduler that features (delayed)
    periodic callbacks.

    Signal and exception handlers are added to manage
    shutdown of threads in adverse cases.
    """

    def __init__(self, max_workers=8):
        # Loop is used for scheduling of tasks
        self.loop = asyncio.get_event_loop()
        # Pool is used for execution of tasks
        self.__executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers)
        self.__tasks = []
        self.exception_caught = False
        self.debug = False

        for s in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            self.loop.add_signal_handler(
                s, lambda s=s: self.loop.create_task(
                    self.__shutdown(self.loop, self.__executor, signal=s))
            )

        self.loop.set_exception_handler(functools.partial(
            self.__handle_exception, self.__executor
        ))

    def add_periodic_task(self, task, unit='hour', run_now=False, periodic_delay=0, round=1, args=()):
        """
        Add periodic task for scheduler.

        :param task: method that will be run periodically
        :param bool run_now: run the task without delay (in debug only, default: False)
                             then run next within 1st period as it would run normally.
        :param str unit: unit of callback (time between calls),
                         value has to be "second", "minute", "hour", or "day"
        :param dict periodic_delay: periodic delay is added waiting time to every period
                                    E.g. expect task to run every day, 5h after midnight
                                    periodic_delay={'hours': 5} and unit='day'
        """
        self.__process_delay(
            periodic_delay)  # Test passed variable only, lambdas are resolved in each period
        self.__tasks.append(self.__periodic_callback(
            self.__executor, task, unit, run_now=run_now, periodic_delay=periodic_delay, round=round, args=args
        ))

    async def __periodic_callback(self, executor, task, unit, run_now=False, round=1, periodic_delay=0, args=()):
        """
        Provide periodic callbacks with delay.

        Run in loop to schedule next run of task based on period intervals.
        When task is scheduled wait for the next period to schedule new run.

        :param task: method that will be run periodically
        :param bool run_now: run the task without delay (in debug only, default: False)
                             then run next within 1st period as it would run normally.
        :param str unit: unit of callback (time between calldelays),
                         value has to be "second", "minute", "hour", or "day"
        :param dict periodic_delay: periodic delay is added waiting time to every period
                                    E.g. expect task to run every day, 5h after midnight
                                    periodic_delay={'hours': 5} and unit='day'
        """
        loop = asyncio.get_event_loop()
        loglevel = 'INFO' if unit == 'day' else 'DEBUG'
        async_sleep = self.__async_sleep if unit in [
            'second', 'minute', 'hour'] else self.__async_hard_sleep
        # Compute time of next run and optimal delay to sleep
        next_run, delay = (
            datetime.now().replace(microsecond=0), 0
        ) if run_now else self.__get_wait_time(unit, round, periodic_delay)
        run_now = False  # Hard reset to avoid looping tasks

        while True:
            # Schedule the task
            task_id = 'period:{}:{}'.format(task.__name__, uuid.uuid4())
            self.log('{} - task scheduled at: {}'.format(task_id,
                                                         next_run), loglevel, source='TASK')
            loop.call_later(delay, self.__callback, executor,
                            task, task_id, next_run, args)
            # Wait the same period before scheduling next run
            await async_sleep(next_run, delay)
            next_run, delay = self.__get_wait_time(unit, round, periodic_delay)

    def add_delayed_task(self, task, unit='hour', run_now=False, delay=0, round=1, args=()):
        """
        Add delayed task.

        Period should usually start with the beginning of next windows.
        After the delay task is assigned to executor pool where
        it is run threadsafe.

        :param int delay: delay in valid timedelta {'hours': 3},
        :param task: method that will be run periodically
        """
        delay = self.__process_delay(delay)
        self.__tasks.append(self.__delayed_callback(
            self.__executor, task, unit, run_now=run_now, delay=delay, round=round, args=args
        ))

    async def __delayed_callback(self, executor, task, unit='hour', run_now=False, delay=0, round=1, args=()):
        """
        Add delayed task.

        Period should usually start with the beginning of next windows.
        After the delay task is assigned to executor pool where
        it is run threadsafe.

        :param task: method that will be run periodically
        :param str unit: unit of callback (time between calls),
                         value has to be "second", "minute", "hour", or "day"
        :param int delay: delay over unit as timedelta {'hours': 3},
        :param int round: rounding unit windows (default every 1st hour)
        :param bool run_now: run the task without delay (in debug only, default: False)
        """
        loop = asyncio.get_event_loop()
        # Compute time of next run and optimal delay to sleep
        next_run, delay = (datetime.now(), 0) if run_now else self.__get_wait_time(
            unit, round, delay)
        task_id = 'delay:{}:{}'.format(task.__name__, uuid.uuid4())
        self.log('{} - task scheduled at: {}'.format(task_id,
                                                     next_run), 'DEBUG', source='TASK')
        loop.call_later(delay, self.__callback, executor,
                        task, task_id, next_run, args)

    def __callback(self, executor, task, task_id, next_run, args):
        """Assign task to executor when called."""
        async def assign_to_executor(executor, task, next_run, args):
            loop = asyncio.get_event_loop()
            await self.__async_sleep(next_run, 0)
            if loop.is_running():
                await loop.run_in_executor(executor, task, *args)
        loop = asyncio.get_event_loop()
        if loop.is_running():
            self.log('{} - task started'.format(task_id),
                     'DEBUG', source='TASK')
            future = asyncio.run_coroutine_threadsafe(
                assign_to_executor(executor, task, next_run, args),
                loop
            )
            future.add_done_callback(functools.partial(
                self.__handle_task_exception, task_id
            ))

    def round_up_time(self, usedate=None, unit='minute', round=1, delay=0):
        """
        Round datetime up to the next nearest period.

        :param datetime usedate: optional (default: datetime.now)
        :param str unit: second, minute, hour, or day (default: "minute")
        :param int round: rounding value in seconds (default: 1)
        :param dict delay: delay in delta format, e.g. {'hours': 2}
                           cannot be longer than `unit` used with
                           regard to rounding.
        :return datetime: next nearest date-time based on the period
        """
        usedate = usedate if isinstance(usedate, datetime) else datetime.now()
        delta = int(timedelta(**{'{}s'.format(unit): round}).total_seconds())
        seconds = (usedate.replace(tzinfo=None) - usedate.min).seconds
        rounding = (seconds + delta) // delta * delta
        delay = delay if delay < delta else 0
        return usedate + timedelta(0, rounding - seconds + delay, -usedate.microsecond)

    async def __async_sleep(self, next_run, delay):
        """
        Sleep and assure next run period.
        """
        await asyncio.sleep(delay)
        while datetime.now() <= next_run:
            # Assure next period window
            await asyncio.sleep(0.2)

    async def __async_hard_sleep(self, next_run, delay):
        """
        Break down long waiting sleep into hours.

        Avoiding limits on asyncio sleeptimes.
        """
        while delay > 3600:
            await asyncio.sleep(3600)
            delay = delay - 3600
            self.log('Heartbeat', "DEBUG")
        await self.__async_sleep(next_run, delay)

    def __process_delay(self, delay):
        if delay:
            processed = dict((k, v()) if callable(v) else (k, v)
                             for k, v in delay.items())
            return int(timedelta(**processed).total_seconds())
        return delay

    def __get_wait_time(self, unit, round, delay):
        processed = self.__process_delay(delay)
        next_run = self.round_up_time(unit=unit, round=round, delay=processed)
        return (next_run, (next_run - datetime.now()).seconds)

    def log(self, message, type='INFO', source='SCHEDULER'):
        """
        Log message to stdout/stderr
        """
        if type == 'DEBUG' and not self.debug:
            return
        fn = sys.stdout if type in ['INFO', 'DEBUG'] else sys.stderr
        fn.write("{} - {} - {} - {}\n".format(
            datetime.now().replace(microsecond=0), source, type, message)
        )

    async def __shutdown(self, loop, executor, signal=None):
        """
        Cleanup tasks tied to the service's shutdown.

        :param pool: asyncio loop
        """
        if signal:
            self.log("Received exit signal {}...".format(signal.name), "WARN")
            if signal.name not in ['SIGINT']:
                self.exception = True  # Only SIGINT can return 0

        self.log("Stopping scheduled tasks", "WARN")
        try:
            await asyncio.sleep(1)
            current = asyncio.Task.current_task(loop)
            tasks = [t for t in asyncio.Task.all_tasks(
                loop) if t is not current]
            [task.cancel() for task in tasks]
            await asyncio.gather(*tasks, return_exceptions=True)
        except concurrent.futures.CancelledError:
            # Shutdown task gets cancelled if too many called, only in debug
            self.log('Task canceled on shutdown', 'DEBUG')

        executor.shutdown(wait=False)
        self.log("Releasing {} threads from executor".format(
            len(executor._threads)))
        for thread in executor._threads:
            try:
                thread._tstate_lock.release()
            except Exception:
                pass

    def __handle_exception(self, executor, loop, context):
        """
        Handle scheduler exceptions.

        :param pool: asyncio loop
        :param context: asyncio handler context
        """
        self.log("Exception handler called", 'ERROR')
        exc = context.get("exception", context["message"])
        self.log('Caught scheduler exception: "{}"'.format(exc), 'ERROR')
        if 'exception' in context:
            ex = context['exception']
            sys.stderr.write(''.join(traceback.format_exception(
                etype=type(ex), value=ex, tb=ex.__traceback__
            )))
        self.exception_caught = True
        if not loop.is_closed() and loop.is_running():
            loop.create_task(self.__shutdown(loop, executor))

    def __handle_task_exception(self, task_id='unknown:task', future=None):
        """
        Handle task exceptions.
        """
        if future and future.cancelled():
            self.log('{} - task cancelled'.format(task_id),
                     'DEBUG', source='TASK')
            return
        if future and future.exception():
            self.log('{} - task raised an exception'.format(task_id),
                     'ERROR', source='TASK')
            if not self.debug:
                return
            ex = future.exception()
            sys.stderr.write(''.join(traceback.format_exception(
                etype=type(ex), value=ex, tb=ex.__traceback__
            )))
        else:
            self.log('{} - task finished'.format(task_id),
                     'DEBUG', source='TASK')

    def run_concurrent(self, handle_exceptions=True, debug=False):
        """
        Run previously added periodic tasks concurrently.

        Exceptions inside tasks have to be handled in the tasks.
        Scheduler doesn't propagate exceptions, they are ignored
        and other tasks continue executing.

        :param bool handle_exceptions: do not fail on task exceptions (default True)
        """
        self.log('Starting scheduler')
        self.debug = debug
        try:
            gathered_tasks = asyncio.gather(
                *self.__tasks,
                return_exceptions=handle_exceptions
            )
            try:
                self.loop.run_until_complete(gathered_tasks)
            finally:
                if self.loop.is_running():
                    self.loop.stop()
                self.loop.close()
            self.log('Completed tasks')
        except Exception as e:
            self.log('Stopping scheduler')
            if self.exception_caught or str(e):
                self.log('Stopped after exception or signal {}'.format(str(e)))
                sys.exit(1)
