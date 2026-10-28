"""
Sketch Kites — local network hub server
Python 3.7+, stdlib only (no pip required)

Usage:
  python scripts/server.py          # default port 8080
  python scripts/server.py 9000     # custom port

API:
  GET  /api/kites          — return all kites as JSON array
  GET  /api/host-info      — return the server's LAN IP + port (for QR code URLs)
  POST /api/submit         — add a new kite; broadcast to all display screens via SSE
  GET  /api/events         — SSE stream (new-kite, remove-kite, clear events)
  POST /api/remove-kite    — remove a single kite by id; broadcast via SSE
  POST /api/clear          — clear all kites; broadcast 'clear' event to display screens

All other paths are served as static files from the project root.
Kites are persisted to kites-data.json.
"""

import json
import os
import queue
import socket
import sys
import threading
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ── Persistence ───────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = PROJECT_ROOT / 'kites-data.json'

kites_lock = threading.Lock()
kites: list = []

def load_kites():
    global kites
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                kites = data
                print(f'  Loaded {len(kites)} kite(s) from {DATA_FILE.name}')
        except Exception as e:
            print(f'  Warning: could not load {DATA_FILE.name}: {e}')

def save_kites():
    """Write kites list to disk. Must be called with kites_lock held."""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(kites, f)

# ── SSE client registry ───────────────────────────────────────────────────────
clients_lock = threading.Lock()
clients: list = []  # list of queue.Queue, one per connected display screen

# ── Host info (set once at startup, read-only afterwards) ─────────────────────
HOST_INFO: dict = {'ip': '127.0.0.1', 'port': 8080}

def broadcast(event: str, data: str):
    """Push an SSE event to every connected display screen."""
    message = f'event: {event}\ndata: {data}\n\n'.encode('utf-8')
    with clients_lock:
        dead = []
        for q in clients:
            try:
                q.put_nowait(message)
            except queue.Full:
                dead.append(q)
        for q in dead:
            clients.remove(q)

# ── Request handler ───────────────────────────────────────────────────────────
class KiteHandler(SimpleHTTPRequestHandler):

    # Silence the default access log — comment out to re-enable
    def log_message(self, format, *args):
        pass

    def _send_json(self, status: int, obj):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            return None
        raw = self.rfile.read(length)
        return json.loads(raw.decode('utf-8'))

    # ── GET ──────────────────────────────────────────────────────────────────

    def do_GET(self):
        if self.path == '/api/kites':
            with kites_lock:
                data = list(kites)
            self._send_json(200, data)

        elif self.path == '/api/events':
            self._handle_sse()

        elif self.path == '/api/host-info':
            self._send_json(200, HOST_INFO)

        else:
            # Serve static files (HTML, images, etc.)
            super().do_GET()

    # ── POST ─────────────────────────────────────────────────────────────────

    def do_POST(self):
        if self.path == '/api/submit':
            try:
                kite = self._read_json_body()
                if not isinstance(kite, dict) or 'dataURL' not in kite:
                    self._send_json(400, {'error': 'Invalid kite data'})
                    return
                with kites_lock:
                    kites.append(kite)
                    save_kites()
                broadcast('new-kite', json.dumps(kite))
                self._send_json(200, {'ok': True})
                name = kite.get('name', 'Unnamed kite')
                print(f'  [+] Kite received: "{name}" (total: {len(kites)})')
            except Exception as e:
                self._send_json(500, {'error': str(e)})

        elif self.path == '/api/clear':
            with kites_lock:
                kites.clear()
                save_kites()
            broadcast('clear', '{}')
            self._send_json(200, {'ok': True})
            print('  [x] All kites cleared')

        elif self.path == '/api/remove-kite':
            try:
                data = self._read_json_body()
                kite_id = data.get('id') if isinstance(data, dict) else None
                if not kite_id:
                    self._send_json(400, {'error': 'Missing kite id'})
                    return
                with kites_lock:
                    before = len(kites)
                    kites[:] = [kite for kite in kites if kite.get('id') != kite_id]
                    removed = len(kites) < before
                    if removed:
                        save_kites()
                if removed:
                    broadcast('remove-kite', json.dumps({'id': kite_id}))
                self._send_json(200, {'ok': True, 'removed': removed})
                print(f'  [-] Kite removed: {kite_id} (total: {len(kites)})')
            except Exception as e:
                self._send_json(500, {'error': str(e)})

        else:
            self._send_json(404, {'error': 'Not found'})

    # ── OPTIONS (CORS preflight) ──────────────────────────────────────────────

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    # ── SSE streams ───────────────────────────────────────────────────────────

    def _handle_sse(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        q = queue.Queue(maxsize=50)
        with clients_lock:
            clients.append(q)

        ping = b': ping\n\n'  # keep-alive comment line (not an event)

        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                    self.wfile.write(msg)
                    self.wfile.flush()
                except queue.Empty:
                    # Send a keep-alive ping so the connection doesn't time out
                    self.wfile.write(ping)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with clients_lock:
                if q in clients:
                    clients.remove(q)

# ── Server (suppresses benign client-disconnect noise) ────────────────────────

_BENIGN_ERRORS = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)

class KiteServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        if issubclass(sys.exc_info()[0], _BENIGN_ERRORS):
            return  # browser closed the connection — not a real error
        super().handle_error(request, client_address)

# ── Local IP helper ───────────────────────────────────────────────────────────

def get_local_ip() -> str:
    try:
        # Connect to a public address without actually sending anything;
        # lets us find the outbound interface IP on the local network.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

    # Serve files from the project root (one level above scripts/)
    os.chdir(PROJECT_ROOT)

    load_kites()

    ip = get_local_ip()
    HOST_INFO['ip'] = ip
    HOST_INFO['port'] = port
    now = datetime.now().strftime('%H:%M')

    print()
    print('  Sketch Kites - Hub Server')
    print(f'  Started at {now}')
    print(f'  Display screen  ->  http://localhost:{port}/display.html')
    print(f'  Kite kiosk      ->  http://{ip}:{port}/upload.html')
    print('  Press Ctrl+C to stop the server')
    print()

    server = KiteServer(('', port), KiteHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        print('\n  Server stopped.')

if __name__ == '__main__':
    main()
