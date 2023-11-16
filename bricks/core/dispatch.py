# -*- coding: utf-8 -*-
# @Time    : 2023-11-13 17:46
# @Author  : Kem
# @Desc    : task distribution
__all__ = (
    "Dispatcher",
    "Task"
)

import asyncio
import ctypes
import itertools
import queue
import sys
import threading
import time
from concurrent.futures import Future
from typing import Union, Dict, Optional

from bricks import const
from bricks.core import events
from bricks.lib import context


class Task(Future):
    """
    A future that is used to store task information

    """

    def __init__(self, func, args=None, kwargs=None, callback=None):
        self.func = func
        self.args = args or []
        self.kwargs = kwargs or {}
        self.callback = callback
        self.dispatcher: Optional["Dispatcher"] = None
        self.worker: Optional["Worker"] = None
        self.future: Optional["asyncio.Future"] = None
        callback and self.add_done_callback(callback)
        super().__init__()

    @property
    def is_async(self):
        return asyncio.iscoroutinefunction(self.func)

    def cancel(self) -> bool:
        self.dispatcher and self.dispatcher.cancel_task(self)
        self.future and self.future.cancel()
        return super().cancel()


class Worker(threading.Thread):
    """
    worker can execute task with threading

    """

    def __init__(self, dispatcher: 'Dispatcher', name: str, daemon=False, trace=False, **kwargs):
        self.dispatcher = dispatcher
        self._shutdown = False
        self.trace = trace
        self._awaken = threading.Event()
        self._awaken.set()
        super().__init__(daemon=daemon, name=name, **kwargs)

    def run(self) -> None:
        self.trace and sys.settrace(self._trace)
        while self.dispatcher.is_running() and not self._shutdown:
            not self.trace and self._awaken.wait()
            try:
                task: Task = self.dispatcher.tasks.get(timeout=5)
                task.worker = self

            except queue.Empty:
                self.dispatcher.stop_worker(self.name)
                return

            try:
                if task.is_async:
                    future = self.dispatcher.create_future(task)
                    task.future = future
                    future.result()
                else:
                    ret = task.func(*task.args, **task.kwargs)
                    task.set_result(ret)

            except (KeyboardInterrupt, SystemExit) as e:
                raise e

            except Exception as e:
                task.set_exception(e)
                events.Event.invoke(context.Error(error=e, form=const.ERROR_OCCURRED))

    def stop(self) -> None:

        if not self.is_alive():
            return

        exc = ctypes.py_object(SystemExit)
        tid = ctypes.c_long(self.ident)

        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, exc)
        if res == 0:
            raise ValueError("nonexistent thread id")
        elif res > 1:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, None)
            raise SystemError("PyThreadState_SetAsyncExc failed")

        self._shutdown = True

    def pause(self) -> None:
        self._awaken.clear()

    def awake(self) -> None:
        self._awaken.set()

    def _trace(self, frame, event, arg):  # noqa
        if event == 'call':
            return self._localtrace
        else:
            return None

    def _localtrace(self, frame, event, arg):  # noqa
        self._awaken.wait()
        if self._shutdown and event == 'line':
            raise SystemExit()
        return self._localtrace


class Dispatcher(threading.Thread):
    """
    Dispatcher: Responsible for task scheduling, assignment and execution

    Outstanding features of this dispatcher:

    1. Supports setting a maximum number of concurrent tasks, and can automatically add or close workers based on the number of currently submitted tasks.
    2. Workers support manual pause and shutdown.
    3. The submitted tasks, whether asynchronous or synchronous, are all efficiently managed.
    4. After submission, a future is returned. To wait for the result, you simply need to call `future.result()`. You can also cancel the task, which simplifies asynchronous programming.


    """

    def __init__(self, max_workers=1, trace=False):
        self.max_workers = max_workers
        self.trace = trace
        self.tasks = queue.Queue()
        self.workers: Dict[str, Worker] = {}
        self.loop = asyncio.get_event_loop()

        self._remain_workers = threading.Semaphore(self.max_workers)
        self._semaphore = threading.Semaphore(self.max_workers)
        self._shutdown = asyncio.Event()
        self._running = threading.Event()
        self._counter = itertools.count()

        super().__init__(daemon=True, name="ForkConsumer")

    def create_worker(self, size: int = 1):
        """
        create workers

        :param size:
        :return:
        """
        for _ in range(size):
            worker = Worker(self, name=f"worker-{next(self._counter)}", trace=self.trace)
            self.workers[worker.name] = worker
            worker.start()
            self._remain_workers.acquire()

    def stop_worker(self, *idents: str):
        """
        stop workers

        :param idents:
        :return:
        """
        for ident in idents:
            worker = self.workers.pop(ident, None)
            worker and self._remain_workers.release()
            worker and worker.stop()

    def pause_worker(self, *idents: str):
        """
        pause workers

        :param idents:
        :return:
        """
        for ident in idents:
            worker = self.workers.get(ident)
            worker and worker.pause()

    def awake_worker(self, *idents: str):
        """
        awake workers

        :param idents:
        :return:
        """
        for ident in idents:
            worker = self.workers.get(ident)
            worker and worker.awake()

    def submit_task(self, task: Task, timeout: int = None) -> Task:
        assert self.is_running(), "dispatcher is not running"

        task.dispatcher = self
        if timeout == -1:
            self.tasks.put(task)
        else:
            self._semaphore.acquire()
            self.tasks.put(task)
            task.add_done_callback(lambda x: self._semaphore.release())

        self.adjust_workers()
        return task

    def adjust_workers(self):
        remain_workers = self._remain_workers._value  # noqa
        remain_tasks = self.tasks.qsize()
        if remain_workers > 0 and remain_tasks > 0:
            self.create_worker(min(remain_workers, remain_tasks))

    def cancel_task(self, task: Task):
        """
        cancel task

        :param task:
        :return:
        """
        with self.tasks.not_empty:
            # If the task is still in the task queue and has not been retrieved for use, it will be deleted from the task queue
            if task in self.tasks.queue:
                self.tasks.queue.remove(task)
                self.tasks.not_full.notify()
            else:
                # If there is a worker and the task is running, shut down the worker
                task.worker and not task.done() and self.stop_worker(task.worker.name)

    def create_future(self, task: Task):
        """
        create a async task future object

        :param task:
        :return:
        """
        assert self.is_running(), "dispatcher is not running"
        assert task.is_async, "task must be async function"
        return asyncio.run_coroutine_threadsafe(task.func(*task.args, **task.kwargs), self.loop)

    @staticmethod
    def make_task(task: Union[dict, Task]) -> Task:
        """
        package a task

        :param task:
        :return:
        :rtype:
        """
        if isinstance(task, Task):
            return task

        func = task.get('func')

        # get positional parameters
        positional = task.get('args', [])
        positional = [positional] if not isinstance(positional, (list, tuple)) else positional

        # get keyword parameters
        keyword = task.get('kwargs', {})
        keyword = {} if not isinstance(keyword, dict) else keyword

        # get callback function
        callback = task.get("callback")

        return Task(
            func=func,
            args=positional,
            kwargs=keyword,
            callback=callback,
        )

    def is_running(self):
        return not self._shutdown.is_set() and self._running.is_set()

    @property
    def running(self):
        return self.max_workers - self._remain_workers._value + self.tasks.qsize()  # noqa

    def run(self):
        async def main():
            self._running.set()
            await self._shutdown.wait()

        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(main())
        try:
            self.loop.close()
        except:
            pass

    def stop(self):
        self.stop_worker(*self.workers.keys())
        # note: It must be called this way, otherwise the thread is unsafe and the write over there cannot be closed
        self.loop.call_soon_threadsafe(self._shutdown.set)

        self._running.clear()

    def start(self) -> None:
        super().start()
        self._running.wait(5)


if __name__ == '__main__':
    dis = Dispatcher()
    dis.start()
    time.sleep(1)
    dis.stop()
    time.sleep(1)
    dis.start()
