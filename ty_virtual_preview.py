#!/usr/bin/env python3
"""TY-VIRTUAL V1.0: static-only PWA preview. No AI, DB, Telegram, or secrets."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
ASSETS = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/virtual': ('virtual.html', 'text/html; charset=utf-8'),
    '/virtual.html': ('virtual.html', 'text/html; charset=utf-8'),
    '/manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json'),
    '/icon-192.png': ('icon-192.png', 'image/png'),
    '/icon-512.png': ('icon-512.png', 'image/png'),
    '/avatar-rest-full.png': ('avatar-rest-full.png', 'image/png'),
    '/avatar-chat-half.png': ('avatar-chat-half.png', 'image/png'),
    '/astra_test.html': ('astra_test.html', 'text/html; charset=utf-8'),
    '/astra_actions.html': ('astra_actions.html', 'text/html; charset=utf-8'),
    '/tu_uyen_robot.glb': ('tu_uyen_robot.glb', 'model/gltf-binary'),
    '/quaternius_test.html': ('quaternius_test.html', 'text/html; charset=utf-8'),
    '/Robot.fbx': ('Robot.fbx', 'application/octet-stream'),
  
}

class PreviewHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/healthz':
            return self.reply_json(200, {'ok': True, 'mode': 'pwa-preview'})
        if path == '/api/auth/status':
            # Preview UI only; never accept founder credentials on this server.
            return self.reply_json(200, {'required': False, 'ready': False, 'preview': True})
        if path.startswith('/api/'):
            return self.reply_json(503, {'ok': False, 'error': 'PWA_PREVIEW_ONLY'})
        asset = ASSETS.get(path)
        if asset is None:
            self.send_error(404)
            return
        filename, mime = asset
        try:
            data = (ROOT / filename).read_bytes()
        except OSError:
            self.send_error(404, 'Missing preview asset')
            return
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if path in ('/astra_test.html', '/astra_actions.html', '/quaternius_test.html'):
            # Allow external Three.js modules only on isolated 3D test pages.
            csp = ("default-src 'self' 'unsafe-inline' data:; "
                   "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net; "
                   "connect-src 'self' blob: https://cdn.jsdelivr.net https://unpkg.com; img-src 'self' data: blob:; "
                   "media-src 'self' data: blob:; worker-src 'self' blob:; "
                   "object-src 'none'; frame-ancestors 'none'")
        else:
            csp = "default-src 'self' 'unsafe-inline' data:; connect-src 'self'; img-src 'self' data:; media-src 'self' data:; object-src 'none'; frame-ancestors 'none'"
        self.send_header('Content-Security-Policy', csp)
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        self.reply_json(503, {'ok': False, 'error': 'PWA_PREVIEW_ONLY'})

    def reply_json(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8000'))
    ThreadingHTTPServer(('0.0.0.0', port), PreviewHandler).serve_forever()
