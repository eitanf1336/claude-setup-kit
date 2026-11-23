#!/usr/bin/env python3
"""Minimal status screen for the home server. Pure stdlib, serves 127.0.0.1:7800.

  GET  /                the page (index.html next to this file), meant for a kiosk browser on the server's own display
  GET  /api/stats       JSON: host, uptime, load, RAM, disk, running user services, and the "running now" list
  POST /api/now         {"key","word","name"?,"detail"?,"ttl"?|"clear":true}  (this is what `server-now` calls)
  GET  /api/health      JSON: when the page last called us (the kiosk-watchdog reads this)

The "running now" list: one live word per project ("working", "listening", "rendering"...).
Words expire (default 30 min) so a crashed job never leaves a stale "working" on the screen.
Extend collect_stats() with your own panels (money, users, a dashboard) and render them in index.html.
"""
import json, os, shutil, subprocess, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get('SCREEN_PORT', '7800'))
HIDE = ('dbus', 'pipewire', 'wireplumber', 'xdg', 'gpg', 'ssh-agent', 'at-spi')
NOW = {}            # key -> {key, name, word, detail, until}
SEEN = {}           # role -> unix time of the page's last call
DEFAULT_TTL = 1800


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ''


def services():
    out = sh(['systemctl', '--user', 'list-units', '--type=service', '--state=running', '--no-legend', '--plain'])
    names = [l.split()[0] for l in out.splitlines() if l.strip()]
    return [n for n in names if not any(h in n for h in HIDE)]


def collect_stats():
    mem = {}
    for line in open('/proc/meminfo'):
        k, v = line.split(':')
        mem[k] = int(v.split()[0]) // 1024
    du = shutil.disk_usage('/')
    now = time.time()
    for k in [k for k, v in NOW.items() if v['until'] < now]:
        del NOW[k]
    return {
        'host': os.uname().nodename,
        'uptime_s': int(float(open('/proc/uptime').read().split()[0])),
        'load': os.getloadavg(),
        'ram_used_mb': mem['MemTotal'] - mem['MemAvailable'], 'ram_total_mb': mem['MemTotal'],
        'disk_used_gb': du.used // 2**30, 'disk_total_gb': du.total // 2**30,
        'services': services(),
        'running': {'items': sorted(NOW.values(), key=lambda i: i['key'])},
        'time': int(now),
    }


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj, ctype='application/json'):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _seen(self, qs):
        for part in qs.split('&'):
            if part.startswith('role='):
                SEEN[part[5:]] = time.time()

    def do_GET(self):
        path, _, qs = self.path.partition('?')
        self._seen(qs)
        if path == '/':
            return self._send(200, open(os.path.join(HERE, 'index.html'), 'rb').read(), 'text/html; charset=utf-8')
        if path == '/api/stats':
            return self._send(200, collect_stats())
        if path == '/api/health':
            return self._send(200, {'blank': False, 'page_seen_ago': {r: time.time() - t for r, t in SEEN.items()}})
        self._send(404, {'error': 'not found'})

    def do_POST(self):
        if self.path != '/api/now':
            return self._send(404, {'error': 'not found'})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        except ValueError:
            return self._send(400, {'error': 'bad json'})
        key = str(body.get('key', '')).strip()
        if not key:
            return self._send(400, {'error': 'key required'})
        if body.get('clear'):
            NOW.pop(key, None)
            return self._send(200, {'ok': True})
        word = str(body.get('word', '')).strip()
        if not word:
            return self._send(400, {'error': 'word required'})
        ttl = int(body.get('ttl') or DEFAULT_TTL)
        NOW[key] = {'key': key, 'name': str(body.get('name') or key), 'word': word[:24],
                    'detail': str(body.get('detail', ''))[:80], 'until': time.time() + ttl}
        self._send(200, {'ok': True})


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
