"""A decoder program (multimon-ng, rtl_433) run beside the app, fed through its stdin.

The radio thread must never wait for it: samples go into a small queue that a writer thread
empties into the program, and when the program falls behind the oldest blocks are dropped.
Its output is read line by line on another thread. When it stops, it is started again on the
next block after a pause, and the reason is kept to show on the page.
"""

import contextlib
import logging
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable

logger = logging.getLogger(__name__)

RESTART_DELAY = 5.0


class Program:
    def __init__(self, name: str, command: list[str], on_line: Callable[[str], None], backlog: int = 40):
        self.name = name
        self.command = command
        self.on_line = on_line
        self.queue: queue.Queue = queue.Queue(maxsize=backlog)
        self.process: subprocess.Popen | None = None
        self.error: str | None = None
        self.dropped = 0
        self._retry_at = 0.0
        self._stderr: deque = deque(maxlen=5)
        self._stopping = False

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        self._stopping = False
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as e:
            self.error = f"{self.name} could not be started: {e}"
            self._retry_at = time.monotonic() + RESTART_DELAY
            logger.error(self.error)
            return
        self.error = None
        logger.info(f"Started {self.name}: {' '.join(self.command)}")
        process = self.process
        threading.Thread(target=self._write, args=(process,), name=f"{self.name}-in", daemon=True).start()
        threading.Thread(target=self._read, args=(process,), name=f"{self.name}-out", daemon=True).start()
        threading.Thread(target=self._read_errors, args=(process,), name=f"{self.name}-err", daemon=True).start()

    def write(self, data: bytes) -> None:
        """Called by the radio thread with every block; never blocks."""
        if not self.running:
            if self._stopping or time.monotonic() < self._retry_at:
                return
            if self.process is not None:
                self._ended()
                return
            self.start()
            if not self.running:
                return
        try:
            self.queue.put_nowait(data)
        except queue.Full:
            # The program falls behind: drop the oldest block rather than delay the radio
            with contextlib.suppress(queue.Empty):
                self.queue.get_nowait()
            self.dropped += 1
            with contextlib.suppress(queue.Full):
                self.queue.put_nowait(data)

    def _ended(self) -> None:
        code = self.process.poll() if self.process else None
        detail = " / ".join(self._stderr) or f"exit code {code}"
        self.error = f"{self.name} stopped: {detail}"
        logger.warning(self.error)
        self.process = None
        self._retry_at = time.monotonic() + RESTART_DELAY

    def _write(self, process: subprocess.Popen) -> None:
        while process.poll() is None:
            try:
                data = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if data is None:
                break
            try:
                process.stdin.write(data)
            except (BrokenPipeError, OSError, ValueError):
                break
        with contextlib.suppress(OSError, ValueError):
            process.stdin.close()

    def _read(self, process: subprocess.Popen) -> None:
        for raw in process.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue
            try:
                self.on_line(line)
            except Exception:
                logger.exception(f"{self.name}: could not handle {line[:120]!r}")

    def _read_errors(self, process: subprocess.Popen) -> None:
        for raw in process.stderr:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                self._stderr.append(line)
                logger.debug(f"{self.name}: {line}")

    def stop(self) -> None:
        self._stopping = True
        process, self.process = self.process, None
        # Wake the writer so it closes the program's stdin
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
        with contextlib.suppress(queue.Full):
            self.queue.put_nowait(None)
        if process is None:
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
