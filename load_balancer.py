
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import cycle
from pathlib import Path
from urllib.parse import urlsplit

from request_counter import RequestCounter


HOST = "0.0.0.0"
PORT = 8080
BACKEND_FILE = Path(__file__).parent / "backends.json"

counter = RequestCounter(window_seconds=30)
backend_lock = threading.Lock()
backend_cycle = None
current_backends = []


def get_backends():
    global backend_cycle, current_backends

    with BACKEND_FILE.open() as file:
        configured = json.load(file)["backends"]

    with backend_lock:
        if configured != current_backends:
            current_backends = configured
            backend_cycle = cycle(current_backends)

        return list(current_backends)


def next_backend(backends):
    global backend_cycle

    with backend_lock:
        return next(backend_cycle)


class LoadBalancerHandler(BaseHTTPRequestHandler):

    def send_json(self, status, data):
        body = json.dumps(data, indent=2).encode()

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path

        if path == "/health":
            self.send_json(200, {"status": "healthy"})
            return

        if path == "/stats":
            stats = counter.get_stats()
            stats["backends"] = get_backends()
            self.send_json(200, stats)
            return

        if path not in ("/", "/calculate"):
            self.send_json(404, {"error": "Not found"})
            return

        counter.record_request()

        try:
            backends = get_backends()
        except (OSError, ValueError, KeyError) as error:
            self.send_json(500, {"error": f"Backend configuration error: {error}"})
            return

        if not backends:
            self.send_json(503, {"error": "No backends configured"})
            return

        for _ in range(len(backends)):
            backend = next_backend(backends)

            try:
                request = urllib.request.Request(
                    backend + self.path,
                    headers={"User-Agent": "CloudLab-LoadBalancer"}
                )

                with urllib.request.urlopen(request, timeout=5) as response:
                    body = response.read()
                    content_type = response.headers.get(
                        "Content-Type", "text/html"
                    )

                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Backend-Server", backend)
                self.end_headers()
                self.wfile.write(body)

                print(f"{self.path} -> {backend}")
                return

            except (urllib.error.URLError, TimeoutError, OSError) as error:
                print(f"Backend unavailable: {backend}: {error}")

        self.send_json(503, {
            "error": "All calculator backends are unavailable"
        })

    def log_message(self, format, *args):
        print(f"[LoadBalancer] {format % args}")


def main():
    server = ThreadingHTTPServer((HOST, PORT), LoadBalancerHandler)

    print(f"Load balancer listening on http://localhost:{PORT}")
    print(f"Initial backends: {get_backends()}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down load balancer...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
