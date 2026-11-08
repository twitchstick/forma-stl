"""Run memory-hungry model processing in a separate, short-lived process.

Loading large STL/3MF files can briefly need gigabytes. Doing that inside the
web server has two problems: several requests can do it at once, and the
memory is rarely handed back to the operating system afterwards. So each job
runs in its own process, one at a time across the whole app. When the process
exits, all of its memory is released. A watchdog stops the process if it goes
over FORMA_WORKER_MEMORY_MB, and the file is reported as too large instead of
exhausting the server's memory.
"""

import multiprocessing
import os
import sys
import threading
import time

MEMORY_MB = max(256, int(os.environ.get('FORMA_WORKER_MEMORY_MB', '2048') or 2048))
TIMEOUT = 180
OVER_LIMIT_EXIT = 86
_JOB_LOCK = threading.Lock()


class WorkerFailed(ValueError):
    """The job could not finish within the memory or time budget."""


def _rss():
    """Current resident memory of this process, in bytes."""
    try:
        with open('/proc/self/statm') as f:
            return int(f.read().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    except OSError:
        import resource

        # Not Linux: fall back to the peak, which is close enough for a guard.
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak if sys.platform == 'darwin' else peak * 1024


def _watchdog(limit):
    while True:
        if _rss() > limit:
            os._exit(OVER_LIMIT_EXIT)
        time.sleep(0.02)


def _child(conn, limit, fn, args):
    threading.Thread(target=_watchdog, args=(limit,), daemon=True).start()
    try:
        conn.send(('ok', fn(*args)))
    except ValueError as exc:
        conn.send(('value_error', str(exc)))
    except BaseException as exc:  # noqa: BLE001 - report anything to the parent
        conn.send(('error', f'{type(exc).__name__}: {exc}'))
    finally:
        conn.close()


def run(fn, *args, memory_mb=None, timeout=TIMEOUT):
    """Call fn(*args) in a fresh process and return its result.

    fn must be a module-level function. ValueError from fn is re-raised as
    ValueError; running out of memory or time raises WorkerFailed.
    """
    limit = (memory_mb or MEMORY_MB) * 1024 * 1024
    context = multiprocessing.get_context('spawn')  # never fork a threaded server
    with _JOB_LOCK:
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=_child, args=(send, limit, fn, args), daemon=True)
        process.start()
        send.close()
        try:
            if not receive.poll(timeout):
                process.kill()
                raise WorkerFailed('This file took too long to process.')
            try:
                status, value = receive.recv()
            except EOFError:
                status, value = 'died', None
        finally:
            receive.close()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join()
    if status == 'ok':
        return value
    if status == 'value_error':
        raise ValueError(value)
    if status == 'died' and process.exitcode == OVER_LIMIT_EXIT:
        raise WorkerFailed(
            f'This file needs more than the {limit // 2**20} MB Forma allows for processing a model. '
            'Raise FORMA_WORKER_MEMORY_MB to allow it.'
        )
    if status == 'died':
        raise WorkerFailed('Processing this file stopped unexpectedly.')
    raise RuntimeError(value)
