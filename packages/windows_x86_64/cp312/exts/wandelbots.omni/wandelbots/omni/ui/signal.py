from __future__ import annotations

import threading
from typing import Callable


class Signal:
    """Lightweight synchronous signal for intra-component communication.

    Usage::

        clicked: Signal = Signal()
        clicked.connect(lambda: print("clicked"))
        clicked.emit()
    """

    def __init__(self) -> None:
        self._listeners: list[Callable] = []
        self._lock = threading.Lock()

    def connect(self, fn: Callable) -> None:
        with self._lock:
            if fn not in self._listeners:
                self._listeners.append(fn)

    def disconnect(self, fn: Callable) -> None:
        with self._lock:
            self._listeners.remove(fn)

    def emit(self, *args, **kwargs) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            fn(*args, **kwargs)

    def clear(self) -> None:
        with self._lock:
            self._listeners.clear()
