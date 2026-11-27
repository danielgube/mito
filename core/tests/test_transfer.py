import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import ssl
import unittest
import urllib.error
from urllib.request import Request
from mito.transfer import download, WebRedirect
from mito.storage import YitoError
from mito.catalog import Catalog


class TransferTests(unittest.TestCase):
    def test_http_download_redirect_and_hash_verification(self):
        payload=b'MITO HTTP payload'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/redirect':
                    self.send_response(302)
                    self.send_header('Location','/payload')
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(payload)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target=Path(tmp)/'download'
                url=f'http://127.0.0.1:{server.server_port}/redirect'
                for algorithm in ('md5','sha1','sha256','sha512'):
                    download(url,target,algorithm+':'+hashlib.new(algorithm,payload).hexdigest())
                    self.assertEqual(target.read_bytes(),payload)
                for expected in (None, '', 'skip'):
                    download(url,target,expected)
                    self.assertEqual(target.read_bytes(),payload)
                with self.assertRaises(YitoError):
                    download(url,target,'0'*64)
                self.assertFalse(target.exists())
                with self.assertRaises(YitoError):
                    download(url,target,'md5:'+'0'*32)
                self.assertFalse(target.exists())
                with self.assertRaises(YitoError):
                    download(url, target, max_bytes=len(payload) - 1)
                self.assertFalse(target.exists())
                download(url, target, max_bytes=len(payload))
                self.assertEqual(target.read_bytes(), payload)
                manifest={'version':'1','url':'http://example.org/app.zip','hash':'0'*64}
                self.assertEqual(Catalog(Path(tmp)).compatibility('main/app',manifest),'Compatible')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_intercepted_portal_page_is_reported_instead_of_a_hash_or_json_error(self):
        # A captive portal redirects every request to its login page (HTML, HTTP 200).
        page=b'<!DOCTYPE html><html><form><input name="magic" type="hidden"></form></html>'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path.startswith('/fgtauth'):
                    self.send_response(200)
                    self.send_header('Content-Type','text/html')
                    self.end_headers()
                    self.wfile.write(page)
                else:
                    self.send_response(303)
                    self.send_header('Location','/fgtauth?magic=1')
                    self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target=Path(tmp)/'download'
                base=f'http://127.0.0.1:{server.server_port}'
                for url, expected in ((base+'/app.zip','0'*64), (base+'/app.zip',None), (base+'/bucket/app.json',None)):
                    with self.subTest(url=url, expected=expected):
                        with self.assertRaises(YitoError) as error:
                            download(url,target,expected)
                        self.assertIn('página web', str(error.exception))
                        self.assertIn('127.0.0.1', str(error.exception))
                        self.assertFalse(target.exists())
                # A requested HTML document is not mistaken for interception.
                download(base+'/fgtauth/index.html',target,None)
                self.assertEqual(target.read_bytes(),page)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_hash_mismatch_names_the_url_size_and_host_without_signed_query(self):
        from mito.transfer import DownloadIntegrityError
        # A proxy replaced the artifact with storage metadata (seen: Azure BlockList XML).
        body=b'\xef\xbb\xbf<?xml version="1.0" encoding="utf-8"?><BlockList></BlockList>'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path.startswith('/asset'):
                    self.send_response(200)
                    self.send_header('Content-Type','application/octet-stream')
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(302)
                    self.send_header('Location','/asset?sig=secret-token')
                    self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target=Path(tmp)/'download'
                url=f'http://127.0.0.1:{server.server_port}/release/jdk.zip?token=abc'
                with self.assertRaises(DownloadIntegrityError) as error:
                    download(url,target,'0'*64,root=Path(tmp))
                message=str(error.exception)
                self.assertIn('Hash incorrecto', message)
                self.assertIn(f'http://127.0.0.1:{server.server_port}/release/jdk.zip', message)
                self.assertIn(str(len(body)), message)
                self.assertNotIn('token=abc', message)
                self.assertNotIn('secret-token', message)
                self.assertFalse(target.exists())
                logs=''.join(p.read_text() for p in Path(tmp).rglob('*.log'))
                self.assertIn('download.hash', logs)
                self.assertNotIn('secret-token', logs)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_proxy_confirmation_page_offers_the_intercepted_origin_without_submitting(self):
        from mito.transfer import DownloadIntegrityError, confirmation_page
        # Proxy notice served in place of the file (shape of a real one, anonymized):
        # a POST form with only a Continue button, relative action and no password.
        page=(b'<!DOCTYPE html><html><head><title>Web Page Blocked</title></head><body>'
              b'<img src="data:image/png;base64,' + b'A'*4096 + b'"><p>Category: example</p>'
              b'<form name="login" method="POST" action="/php/urlblock.php?args=x&amp;url=https://evil.example/">'
              b'<input type="submit" name="ok" value="Continue"></form></body></html>')
        posts=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type','text/html')
                self.end_headers()
                self.wfile.write(page)
            def do_POST(self):
                posts.append(self.path)
                self.send_response(200)
                self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target=Path(tmp)/'download'
                base=f'http://127.0.0.1:{server.server_port}'
                for expected in ('0'*64, None):
                    with self.subTest(expected=expected):
                        with self.assertRaises(DownloadIntegrityError) as error:
                            download(base+'/release/app.zip?sig=secret',target,expected)
                        exc=error.exception
                        self.assertTrue(exc.confirm)
                        self.assertEqual(exc.browse, base+'/')
                        self.assertIn('navegador', str(exc))
                        self.assertNotIn('INTERNET', str(exc))
                        self.assertNotIn('secret', str(exc)+exc.browse)
                        self.assertFalse(target.exists())
                self.assertEqual(posts, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        # Login portals and plain block pages keep the previous classification.
        self.assertFalse(confirmation_page(b'<form method="post"><input type="password" name="p"><button>OK</button></form>'))
        self.assertFalse(confirmation_page(b'<html><p>Blocked</p></html>'))
        self.assertFalse(confirmation_page(b'<form><input type="submit"></form>'))
        self.assertTrue(confirmation_page(b"<FORM action='/c' METHOD=post><button type=submit>Continuar</button></FORM>"))

    def test_web_page_detection(self):
        from mito.transfer import web_page
        self.assertTrue(web_page('https://x/app.zip','application/octet-stream',b'\xef\xbb\xbf\n<!doctype html><p>'))
        self.assertTrue(web_page('https://x/app.json','text/html',b'<div>blocked</div>'))
        self.assertFalse(web_page('https://x/app.json','text/html',b'{"version": "1"}'))
        self.assertFalse(web_page('https://x/app.zip','text/html',b'PK\x03\x04'))
        self.assertFalse(web_page('https://x/app.xml','application/xml',b'<?xml version="1.0"?>'))

    def test_optional_and_mixed_hashes(self):
        from mito.transfer import download_hashes, hash_verifier
        urls = ['http://example.org/one.zip','http://example.org/two.zip']
        self.assertEqual(download_hashes(urls, None), [None,None])
        self.assertEqual(download_hashes(urls, 'skip'), [None,None])
        self.assertEqual(download_hashes(urls, ['skip','md5:'+'a'*32]), [None,'md5:'+'a'*32])
        self.assertEqual(hash_verifier('   '), (None,None))
        with self.assertRaises(YitoError):
            download_hashes(urls, ['a'*64])
        with self.assertRaises(YitoError):
            hash_verifier('md5:invalid')
        manifest = {'version':'1','url':urls}
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Catalog(Path(tmp))
            for value in (None, '', 'skip', [None,'md5:'+'a'*32]):
                self.assertEqual(catalog.compatibility('main/app',dict(manifest,hash=value)), 'Compatible')

    def test_redirect_allows_http_and_rejects_other_schemes(self):
        handler=WebRedirect()
        req=Request('https://example.org/app.zip')
        redirected=handler.redirect_request(req,None,302,'Found',{},'http://example.org/app.zip')
        self.assertEqual(redirected.full_url,'http://example.org/app.zip')
        with self.assertRaises(YitoError):
            handler.redirect_request(req,None,302,'Found',{},'file:///app.zip')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(YitoError):
                download('file:///app.zip',Path(tmp)/'app.zip')

    def test_download_retries_without_certificate_verification_when_hash_declared(self):
        fixtures = Path(__file__).parent / 'fixtures'
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(fixtures / 'tls_cert.pem'), str(fixtures / 'tls_key.pem'))
        payload = b'MITO TLS payload'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(payload)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / 'download'
                url = f'https://127.0.0.1:{server.server_port}/payload'
                digest = hashlib.sha256(payload).hexdigest()
                download(url, target, digest)
                self.assertEqual(target.read_bytes(), payload)
                # Sin hash declarado también reintenta: prioriza funcionar en redes
                # corporativas; esas descargas ya exigen confirmación y HTTP está permitido.
                download(url, target, None)
                self.assertEqual(target.read_bytes(), payload)
                # Un hash incorrecto sigue descartando la descarga aunque el certificado no se verifique.
                with self.assertRaises(YitoError):
                    download(url, target, '0' * 64)
                self.assertFalse(target.exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_download_retries_transient_errors_but_not_permanent(self):
        payload = b'MITO retry payload'
        counters = {'gateway': 0, 'down': 0, 'missing': 0}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                if self.path == '/gateway':
                    counters['gateway'] += 1
                    if counters['gateway'] <= 2:
                        self.send_response(504)
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(payload)
                elif self.path == '/down':
                    counters['down'] += 1
                    self.send_response(504)
                    self.end_headers()
                else:
                    counters['missing'] += 1
                    self.send_response(404)
                    self.end_headers()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / 'download'
                base = f'http://127.0.0.1:{server.server_port}'
                digest = hashlib.sha256(payload).hexdigest()
                # 504 transitorio de un gateway/proxy: reintenta y completa la descarga.
                download(f'{base}/gateway', target, digest, attempts=5, delay=0)
                self.assertEqual(target.read_bytes(), payload)
                self.assertEqual(counters['gateway'], 3)
                # 404 permanente: aborta en el primer intento, sin reintentar.
                with self.assertRaises(urllib.error.HTTPError):
                    download(f'{base}/missing', target, attempts=5, delay=0)
                self.assertEqual(counters['missing'], 1)
                self.assertFalse(target.exists())
                # Agotados los reintentos, el último error se propaga y se descarta el archivo.
                with self.assertRaises(urllib.error.HTTPError):
                    download(f'{base}/down', target, attempts=3, delay=0)
                self.assertEqual(counters['down'], 3)
                self.assertFalse(target.exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def serve(self, handler):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join()
        self.addCleanup(stop)
        return f'http://127.0.0.1:{server.server_port}'

    def test_interrupted_download_resumes_with_range_from_received_bytes(self):
        payload = bytes(range(256)) * 64
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                requests.append({'range': self.headers.get('Range'), 'if-range': self.headers.get('If-Range')})
                validator = '"v1"' if self.path == '/etag' else None
                wanted = self.headers.get('Range')
                if wanted and self.path != '/ignores-range':
                    start = int(wanted.removeprefix('bytes=').rstrip('-'))
                    self.send_response(206)
                    self.send_header('Content-Range', f'bytes {start}-{len(payload) - 1}/{len(payload)}')
                    self.send_header('Content-Length', str(len(payload) - start))
                    if validator: self.send_header('ETag', validator)
                    self.end_headers()
                    self.wfile.write(payload[start:])
                    return
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                if validator: self.send_header('ETag', validator)
                self.end_headers()
                # First full response stops halfway; HTTP/1.0 then closes the connection.
                self.wfile.write(payload if len(requests) > 1 else payload[:len(payload) // 2])
        base = self.serve(Handler)
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'download'
            # ETag validator, no hash: resumes with Range + If-Range.
            download(f'{base}/etag', target, None, delay=0)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(requests[1], {'range': f'bytes={len(payload) // 2}-', 'if-range': '"v1"'})
            # Declared hash, no validator: resumes with Range only.
            requests.clear()
            download(f'{base}/plain', target, digest, delay=0)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(requests[1]['range'], f'bytes={len(payload) // 2}-')
            # Neither validator nor hash: the content cannot be checked, so it restarts.
            requests.clear()
            download(f'{base}/plain', target, None, delay=0)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual([r['range'] for r in requests], [None, None])
            # A server that ignores Range answers 200: start again, never append.
            requests.clear()
            download(f'{base}/ignores-range', target, digest, delay=0)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(len(requests), 2)

    def test_cancellation_interrupts_a_stalled_read_and_discards_the_partial_file(self):
        import time
        from mito.operation import Cancelled, Operation, monitored
        release = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '100000')
                self.end_headers()
                self.wfile.write(b'x' * 1000)
                self.wfile.flush()
                release.wait(20)
        base = self.serve(Handler)
        self.addCleanup(release.set)
        progressed = threading.Event()
        operation = Operation(lambda state: progressed.set() if state.get('received') else None)
        threading.Thread(target=lambda: progressed.wait(20) and operation.cancel(), daemon=True).start()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'download'
            started = time.monotonic()
            with monitored(operation), self.assertRaises(Cancelled):
                download(f'{base}/stall', target, None, delay=0)
            # The read timeout is 60 s; cancelling must not wait for it.
            self.assertLess(time.monotonic() - started, 15)
            self.assertFalse(target.exists())
