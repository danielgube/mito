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
