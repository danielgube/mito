from .i18n import tr
import hashlib
import http.client
import queue
import re
from pathlib import Path
import shutil
import ssl
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from .storage import YitoError, inside, portable_path

MAX_DOWNLOAD = 4 * 1024**3
MAX_EXPANDED = 12 * 1024**3


def download_filename(url):
    """Resolve a stable local basename, independent of redirects/HTTP headers."""
    parsed = urllib.parse.urlsplit(url)
    name = urllib.parse.unquote(parsed.fragment[1:] if parsed.fragment.startswith('/')
                               else parsed.path.rsplit('/', 1)[-1])
    portable_path(name)
    if not name or name in ('.', '..') or '/' in name or '\\' in name:
        raise YitoError(tr('Nombre no válido: {p1}', p1=repr(name)))
    return name


class WebRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme not in ('http', 'https'):
            raise YitoError(tr('La redirección debe usar HTTP o HTTPS'))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


MARKUP = (b'<!doctype html', b'<html', b'<head', b'<body', b'<script', b'<meta', b'<title')


def web_page(url, content_type, head):
    """True when a portal or proxy block page replaced a non-HTML resource."""
    if urllib.parse.urlsplit(url).path.lower().endswith(('.htm', '.html', '.xhtml')):
        return False
    text = head.lstrip(b'\xef\xbb\xbf \t\r\n').lower()
    return text.startswith(MARKUP) or ((content_type or '').lower() == 'text/html' and text.startswith(b'<'))


FORM = re.compile(rb'<form\b[^>]*\bmethod\s*=\s*["\']?post\b', re.IGNORECASE)
PASSWORD = re.compile(rb'<input\b[^>]*\btype\s*=\s*["\']?password\b', re.IGNORECASE)
SUBMIT = re.compile(rb'<(?:input\b[^>]*\btype\s*=\s*["\']?submit\b|button\b)', re.IGNORECASE)


def confirmation_page(page):
    """True for a proxy notice that only asks to accept a warning (POST form, no password).

    Generic shape, no vendor rules. MITO never submits it: the user accepts it in
    the browser, where the proxy records that decision."""
    return bool(FORM.search(page) and SUBMIT.search(page) and not PASSWORD.search(page))


class DownloadIntegrityError(YitoError):
    """The received content is not the declared artifact; the UI explains it in a dialog.

    url is the requested URL without query, host the final server and portal marks
    an intercepted HTML page (authentication portal) instead of altered content.
    confirm marks a proxy page asking to accept a warning; browse is the origin of
    the intercepted server to open in the browser (never a URL taken from the page)."""
    def __init__(self, message, url='', size=None, host='', portal=False, confirm=False, browse=''):
        super().__init__(message)
        self.url, self.size, self.host, self.portal = url, size, host, portal
        self.confirm, self.browse = confirm, browse


def public_url(url):
    """Requested URL without query or fragment: signed tokens never reach messages."""
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))


def web_page_error(final_url, url='', size=None, page=b''):
    parsed = urllib.parse.urlsplit(final_url)
    host = parsed.hostname or '?'
    if parsed.scheme in ('http', 'https') and parsed.hostname and confirmation_page(page):
        # The origin, not the artifact path: accepting in the browser must not start the download there.
        browse = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, '/', '', ''))
        return DownloadIntegrityError(tr('El proxy de la red pide confirmar el acceso a {p1} antes de descargar. Ábrelo en el navegador, acepta el aviso y reintenta.',
                            p1=host), url=public_url(url or final_url), size=size, host=host, portal=True,
                            confirm=True, browse=browse)
    return DownloadIntegrityError(tr('La red devolvió una página web en lugar del archivo ({p1}); puede ser el portal de autenticación o un bloqueo del proxy. Pulsa INTERNET y reintenta.',
                        p1=host), url=public_url(url or final_url), size=size, host=host, portal=True)


def _page(path, limit=1024 * 1024):
    """Start of an intercepted page, read only to classify it."""
    try:
        with path.open('rb') as page:
            return page.read(limit)
    except OSError:
        return b''


def hash_verifier(expected):
    if isinstance(expected, str):
        expected = expected.strip()
    if expected is None or expected == '' or (isinstance(expected, str) and expected.strip().lower() == 'skip'):
        return None, None
    if not isinstance(expected, str):
        raise YitoError(tr('El hash debe ser texto, null o skip'))
    expected = expected.strip().lower()
    if ':' in expected:
        algorithm, digest = expected.split(':', 1)
    else:
        digest = expected
        algorithm = {32:'md5', 40:'sha1', 64:'sha256', 128:'sha512'}.get(len(digest), '')
    try:
        hasher = hashlib.new(algorithm, usedforsecurity=False)
    except ValueError:
        raise YitoError(tr('Algoritmo de hash no soportado: {p1}', p1=(algorithm))) from None
    if not hasher.digest_size or not re.fullmatch(r'[a-f0-9]{' + str(hasher.digest_size * 2) + '}', digest):
        raise YitoError(tr('Formato de hash incorrecto'))
    return hasher, digest


def download_hashes(urls, value):
    if value is None or value == '' or (isinstance(value, str) and value.strip().lower() == 'skip'):
        return [None] * len(urls)
    hashes = value if isinstance(value, list) else [value]
    if len(hashes) != len(urls):
        raise YitoError(tr('La cantidad de hashes debe coincidir con las URL'))
    return [None if hash_verifier(expected)[0] is None else expected for expected in hashes]


def _insecure_https_handler():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return urllib.request.HTTPSHandler(context=ctx)


def _build_opener(root, *, insecure=False):
    handlers = [WebRedirect()]
    if insecure:
        handlers.append(_insecure_https_handler())
    if root is None:
        return urllib.request.build_opener(*handlers)
    from .network import opener as proxy_opener
    return proxy_opener(root, None, *handlers)


def _content_range(headers):
    """(start, complete size or None) of a 206 response, None when malformed."""
    match = re.fullmatch(r'\s*bytes\s+(\d+)-(\d+)/(\d+|\*)\s*', (headers.get('Content-Range') or '') if headers else '')
    if not match:
        return None
    return int(match[1]), None if match[3] == '*' else int(match[3])


def _validator(headers):
    """Value for If-Range: a strong ETag or Last-Modified, so a changed file restarts."""
    if not headers:
        return None
    etag = (headers.get('ETag') or '').strip()
    if etag and not etag.startswith('W/'):
        return etag
    return (headers.get('Last-Modified') or '').strip() or None


class _Stream:
    """Network side of one attempt, in its own daemon thread.

    On Windows nothing reliably wakes a blocked socket read from another thread
    (shutdown does not), so the caller never reads: it waits on a queue and can
    leave at once on cancellation. The abandoned thread ends at the next data or
    at the socket timeout, and only it touches and closes the response."""
    def __init__(self, opener, request, timeout=60):
        self.events = queue.Queue(maxsize=16)
        self.stop = threading.Event()
        threading.Thread(target=self._run, args=(opener, request, timeout),
                         name='mito-download', daemon=True).start()

    def _put(self, item):
        while not self.stop.is_set():
            try:
                self.events.put(item, timeout=.25)
                return True
            except queue.Full:
                pass
        return False

    def _run(self, opener, request, timeout):
        try:
            with opener.open(request, timeout=timeout) as response:
                if not self._put(('response', response)):
                    return
                # read1 returns what has arrived instead of waiting for a full block,
                # so progress keeps moving on slow links.
                read = getattr(response, 'read1', response.read)
                while not self.stop.is_set():
                    chunk = read(256 * 1024)
                    if not self._put(('chunk', chunk)) or not chunk:
                        return
        except BaseException as exc:
            self._put(('error', exc))

    def next(self, check=None):
        while True:
            if check is not None:
                check()
            try:
                kind, value = self.events.get(timeout=.25)
            except queue.Empty:
                continue
            if kind == 'error':
                raise value
            return value

    def close(self):
        self.stop.set()


def download(url: str, destination: Path, expected: str | None = None, *, root=None,
             max_bytes=MAX_DOWNLOAD, attempts=5, delay=2, resume=False):
    """Download with retries. After an interruption it resumes with Range from the
    bytes already received when the server allows it and the result stays verifiable
    (If-Range validator or declared hash); otherwise it starts again. An attempt that
    received at least 1 MiB restores the retry budget, so slow but advancing
    connections are not abandoned. The thread's operation (see operation.py)
    receives progress and can cancel at any time, also during a stalled read.

    resume=True continues an existing destination left by an earlier session (for
    example after closing MITO abruptly). Only with a declared hash: without it the
    bytes already on disk could not be verified, so the download starts again."""
    from . import operation
    if urllib.parse.urlsplit(url).scheme not in ('http', 'https'):
        raise YitoError(tr('Las descargas requieren HTTP o HTTPS'))
    has_hash = hash_verifier(expected)[0] is not None
    destination.parent.mkdir(parents=True, exist_ok=True)
    address = url.split('#')[0]
    monitor = operation.current()
    # Salidas de log: se mantienen en español, sin pasar por i18n.
    if has_hash:
        cert_notice = ('Aviso: certificado TLS no verificable; reintentando sin verificarlo '
                       '(integridad garantizada por el hash declarado)')
    else:
        cert_notice = ('Aviso: certificado TLS no verificable; reintentando sin verificarlo '
                       'ni comprobar la integridad (descarga sin hash)')
    attempts = max(1, attempts)
    insecure = False
    remaining = attempts
    pause = max(0.0, delay)
    # State kept across attempts so an interrupted transfer can resume.
    received, length, validator, resumed = 0, None, None, False
    hasher, digest = hash_verifier(expected)
    head, content_type, final_url = b'', '', address
    if resume and has_hash and destination.is_file() and 0 < destination.stat().st_size <= max_bytes:
        # Rebuild the in-memory state of the earlier session from the bytes on disk.
        with destination.open('rb') as previous:
            while block := previous.read(1024 * 1024):
                if len(head) < 512:
                    head += block[:512 - len(head)]
                hasher.update(block)
                received += len(block)
        if hasher.copy().hexdigest().lower() == digest:
            # Completed earlier but not yet renamed by the caller.
            if web_page(address, content_type, head):
                page = _page(destination)
                destination.unlink(missing_ok=True)
                raise web_page_error(final_url, address, received, page)
            return
        # A mismatch after joining parts downloads the whole file once.
        resumed = True
    elif resume:
        # Without a hash (or with an unusable file) earlier bytes cannot be trusted.
        destination.unlink(missing_ok=True)

    def warn(message):
        print(message, flush=True)
        operation.note(message)

    def retry(message):
        nonlocal pause
        warn(f'{message}; reintentando la descarga ({attempts - remaining + 1}/{attempts})')
        try:
            operation.wait(pause)
        except operation.Cancelled:
            destination.unlink(missing_ok=True)
            raise
        pause *= 2

    def stop_if_cancelled():
        if monitor is not None and monitor.cancelled:
            destination.unlink(missing_ok=True)
            monitor.check()

    while True:
        remaining -= 1
        stop_if_cancelled()
        resumable = (received > 0 and (validator is not None or has_hash)
                     and destination.is_file() and destination.stat().st_size == received)
        headers = {'User-Agent': 'MITO/0.1'}
        if resumable:
            headers['Range'] = f'bytes={received}-'
            if validator is not None:
                headers['If-Range'] = validator
        request = urllib.request.Request(address, headers=headers)
        before = received
        opener = _build_opener(root, insecure=insecure)
        stream = _Stream(opener, request)
        check = monitor.check if monitor is not None else None
        try:
            try:
                response = stream.next(check)
                response_headers = getattr(response, 'headers', None)
                partial = getattr(response, 'status', 200) == 206
                span = _content_range(response_headers) if partial else None
                if partial and not (resumable and span is not None and span[0] == received):
                    # A fragment that does not continue the file: next attempt from zero.
                    received, length, validator = 0, None, None
                    raise http.client.HTTPException('respuesta parcial inesperada del servidor')
                if partial:
                    length = span[1] if span[1] is not None else length
                    mode, resumed = 'ab', True
                    warn(f'Aviso: reanudando la descarga desde {received} bytes')
                else:
                    if resumable:
                        warn('Aviso: el servidor no admite reanudar; la descarga empieza de nuevo')
                    received = before = 0
                    resumed = False
                    hasher, digest = hash_verifier(expected)
                    head = b''
                    final_url = getattr(response, 'geturl', lambda: None)() or address
                    content_type = (response_headers.get_content_type()
                                    if hasattr(response_headers, 'get_content_type') else '')
                    declared = (response_headers.get('Content-Length') or '').strip() if response_headers else ''
                    length = int(declared) if declared.isdigit() else None
                    validator = _validator(response_headers)
                    mode = 'wb'
                operation.transfer(received, length)
                # The first block is always reported: a link that stalls right after
                # it must not leave the dialog (and cancellation) showing nothing.
                reported = 0.0
                with destination.open(mode) as output:
                    while chunk := stream.next(check):
                        if received + len(chunk) > max_bytes:
                            if max_bytes == MAX_DOWNLOAD:
                                raise YitoError(tr('Descarga excede el límite de 4 GiB'))
                            raise YitoError(f'Descarga excede el límite de {max_bytes} bytes')
                        output.write(chunk)
                        if len(head) < 512:
                            head += chunk[:512 - len(head)]
                        if hasher is not None:
                            hasher.update(chunk)
                        received += len(chunk)
                        if time.monotonic() - reported >= 0.25:
                            operation.transfer(received, length)
                            reported = time.monotonic()
                operation.transfer(received, length)
                if length is not None and received < length:
                    raise http.client.IncompleteRead(b'', length - received)
            finally:
                stream.close()
        except operation.Cancelled:
            destination.unlink(missing_ok=True)
            raise
        except urllib.error.HTTPError as exc:
            exc.close()
            stop_if_cancelled()
            if resumable and exc.code == 416 and remaining:
                # The partial file no longer matches the server: start again.
                received, length = 0, None
                warn('Aviso: el servidor rechazó reanudar; la descarga empieza de nuevo')
                continue
            # Errores transitorios de servidor, proxy o gateway: se reintentan.
            # Errores 4xx (salvo 408 y 429) son permanentes y abortan sin reintento.
            if remaining and (exc.code >= 500 or exc.code in (408, 429)):
                retry(f'Aviso: HTTP {exc.code}')
                continue
            destination.unlink(missing_ok=True)
            raise
        except urllib.error.URLError as exc:
            stop_if_cancelled()
            if isinstance(getattr(exc, 'reason', None), ssl.SSLCertVerificationError) and not insecure:
                insecure = True
                warn(cert_notice)
                continue
            if remaining:
                retry(f'Aviso: error de red ({exc.reason})')
                continue
            destination.unlink(missing_ok=True)
            raise
        except (OSError, http.client.HTTPException) as exc:
            stop_if_cancelled()
            # Cortes de conexión, timeouts y lecturas incompletas: se reintentan
            # conservando lo recibido para reanudar. Si el intento avanzó, el
            # presupuesto de reintentos y la espera vuelven a empezar.
            if received - before >= 1024 * 1024:
                remaining, pause = attempts, max(0.0, delay)
            if remaining:
                retry(f'Aviso: conexión interrumpida ({exc})')
                continue
            destination.unlink(missing_ok=True)
            raise
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        else:
            total = received
            # Checked before the hash and also without one: a hash-less install
            # must never publish an intercepted HTML page as the component.
            if web_page(request.full_url, content_type, head):
                page = _page(destination)
                destination.unlink(missing_ok=True)
                raise web_page_error(final_url, request.full_url, total, page)
            if hasher is not None and hasher.hexdigest().lower() != digest:
                if resumed and remaining:
                    # Joining parts of a file that changed meanwhile: one full download decides.
                    warn('Aviso: el hash no coincide tras reanudar; la descarga empieza de nuevo')
                    received, length, validator, resumed = 0, None, None, False
                    destination.unlink(missing_ok=True)
                    continue
                destination.unlink(missing_ok=True)
                if root is not None:
                    from .diagnostics import write
                    write(root, 'warning', 'download.hash', 'Hash mismatch', host=urllib.parse.urlsplit(final_url).hostname,
                          bytes=total, content_type=content_type, algorithm=hasher.name)
                host = urllib.parse.urlsplit(final_url).hostname or '?'
                raise DownloadIntegrityError(tr('Hash incorrecto en {p1}: se recibieron {p2} bytes desde {p3}. Un proxy o antivirus puede estar alterando la descarga; prueba desde otra red o avisa a soporte con esta URL. Se ha descartado la descarga.',
                                                p1=public_url(request.full_url), p2=total, p3=host),
                                             url=public_url(request.full_url), size=total, host=host)
            return


def extract_zip(archive: Path, destination: Path):
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > 150000 or sum(x.file_size for x in entries) > MAX_EXPANDED:
            raise YitoError(tr('Archivo demasiado grande para extraer'))
        seen = set()
        for entry in entries:
            target = inside(destination, entry.filename)
            key = str(target).casefold()
            if key in seen:
                raise YitoError(tr('Entrada duplicada: {p1}', p1=(entry.filename)))
            seen.add(key)
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise YitoError(tr('Los enlaces del archivo no están permitidos'))
        for entry in entries:
            target = inside(destination, entry.filename)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as inp, target.open('wb') as out:
                    shutil.copyfileobj(inp, out)
