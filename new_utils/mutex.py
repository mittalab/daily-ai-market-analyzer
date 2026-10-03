"""
Cross-process and cross-thread mutex utility.

Ensures scheduled jobs and background pipeline runs do not execute concurrently,
preventing duplicate runs and duplicate Telegram notifications.
"""
import functools
import inspect
import logging
import os
import sys
import threading
from typing import Callable, Any

logger = logging.getLogger(__name__)

_thread_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _get_thread_lock(name: str) -> threading.Lock:
    with _guard:
        if name not in _thread_locks:
            _thread_locks[name] = threading.Lock()
        return _thread_locks[name]


class JobMutex:
    """
    Cross-process and cross-thread non-blocking mutex.
    On Windows, uses Win32 named mutex (Local\\ namespace) with fallback to file locking.
    On Unix/Linux, uses fcntl.flock on a lockfile in tmp/locks.
    """

    def __init__(self, name: str):
        # Sanitise name for mutex / lockfile name
        self.name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)
        self._thread_lock = _get_thread_lock(self.name)
        self._thread_acquired = False
        self._win_handle = None
        self._file_handle = None
        self._is_acquired = False

    def acquire(self) -> bool:
        """
        Attempt to acquire the mutex in non-blocking mode.
        Returns True if acquired successfully, False if already held.
        """
        # Step 1: In-process thread lock
        if not self._thread_lock.acquire(blocking=False):
            logger.warning("Mutex '%s': already held by another thread in this process", self.name)
            return False
        self._thread_acquired = True

        # Step 2: Cross-process OS lock
        try:
            if sys.platform == "win32":
                try:
                    import win32event
                    import win32api
                    import winerror

                    # Mutex name in Local session namespace
                    mutex_name = f"Local\\DMA_Mutex_{self.name}"
                    handle = win32event.CreateMutex(None, True, mutex_name)
                    last_err = win32api.GetLastError()
                    if last_err == winerror.ERROR_ALREADY_EXISTS:
                        logger.warning("Mutex '%s': already held by another process (ERROR_ALREADY_EXISTS)", self.name)
                        win32api.CloseHandle(handle)
                        self._release_thread_lock()
                        return False
                    self._win_handle = handle
                except ImportError:
                    # Fallback to file locking on Windows
                    if not self._acquire_file_lock():
                        self._release_thread_lock()
                        return False
            else:
                # Unix/Linux file locking
                if not self._acquire_file_lock():
                    self._release_thread_lock()
                    return False
        except Exception as exc:
            logger.error("Mutex '%s': OS acquisition error: %s", self.name, exc)
            self._release_thread_lock()
            return False

        self._is_acquired = True
        return True

    def _acquire_file_lock(self) -> bool:
        locks_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tmp", "locks")
        os.makedirs(locks_dir, exist_ok=True)
        lock_path = os.path.join(locks_dir, f"{self.name}.lock")
        try:
            f = open(lock_path, "a+")
            if sys.platform == "win32":
                import msvcrt
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                except (OSError, IOError):
                    f.close()
                    return False
            else:
                import fcntl
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (OSError, IOError):
                    f.close()
                    return False
            self._file_handle = f
            return True
        except Exception as exc:
            logger.warning("Mutex '%s': file lock failed: %s", self.name, exc)
            return False

    def release(self) -> None:
        """Release the mutex if acquired."""
        if not self._is_acquired and not self._thread_acquired:
            return

        if self._win_handle:
            try:
                import win32event
                import win32api
                win32event.ReleaseMutex(self._win_handle)
                win32api.CloseHandle(self._win_handle)
            except Exception as exc:
                logger.warning("Mutex '%s': error releasing Win32 handle: %s", self.name, exc)
            self._win_handle = None

        if self._file_handle:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(self._file_handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._file_handle.fileno(), fcntl.LOCK_UN)
                self._file_handle.close()
            except Exception as exc:
                logger.warning("Mutex '%s': error releasing file lock: %s", self.name, exc)
            self._file_handle = None

        self._release_thread_lock()
        self._is_acquired = False

    def _release_thread_lock(self) -> None:
        if self._thread_acquired:
            self._thread_acquired = False
            try:
                self._thread_lock.release()
            except RuntimeError:
                pass

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


def job_mutex(name: str) -> Callable:
    """
    Decorator for jobs/functions to enforce single-runner mutual exclusion.
    If the mutex cannot be acquired, logs a warning and returns immediately (skips execution).
    Supports both sync and async functions.
    """
    def decorator(fn: Callable) -> Callable:
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                mutex = JobMutex(name)
                if not mutex.acquire():
                    logger.warning(
                        "Job '%s' mutex could not be acquired (concurrent run in progress). Aborting this run.",
                        name,
                    )
                    return None
                try:
                    return await fn(*args, **kwargs)
                finally:
                    mutex.release()
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                mutex = JobMutex(name)
                if not mutex.acquire():
                    logger.warning(
                        "Job '%s' mutex could not be acquired (concurrent run in progress). Aborting this run.",
                        name,
                    )
                    return None
                try:
                    return fn(*args, **kwargs)
                finally:
                    mutex.release()
            return sync_wrapper
    return decorator
