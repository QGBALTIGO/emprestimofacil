"""Local test server with sufficient queue capacity for parallel browser projects."""
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class TestServer(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('port', type=int, default=4173, nargs='?')
    args = parser.parse_args()
    with TestServer(('127.0.0.1', args.port), SimpleHTTPRequestHandler) as server:
        server.serve_forever()
