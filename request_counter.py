
import threading
import time
from collections import deque


class RequestCounter:
    def __init__(self, window_seconds=30):
        self.total_requests = 0
        self.window_seconds = window_seconds
        self.timestamps = deque()
        self.lock = threading.Lock()

    def record_request(self):
        now = time.time()

        with self.lock:
            self.total_requests += 1
            self.timestamps.append(now)
            self._remove_old_requests(now)

    def _remove_old_requests(self, now):
        cutoff = now - self.window_seconds

        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.popleft()

    def get_stats(self):
        now = time.time()

        with self.lock:
            self._remove_old_requests(now)

            return {
                "requests_total": self.total_requests,
                "requests_in_window": len(self.timestamps),
                "window_seconds": self.window_seconds
            }
