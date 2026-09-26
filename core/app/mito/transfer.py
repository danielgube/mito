from .i18n import tr
import hashlib
import http.client
import re
from pathlib import Path
import shutil
import ssl
import stat
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


def download(url: str, destination: Path, expected: str | None = None, *, root=None,
             max_bytes=MAX_DOWNLOAD, attempts=5, delay=2):
    if urllib.parse.urlsplit(url).scheme not in ('http', 'https'):
        raise YitoError(tr('Las descargas requieren HTTP o HTTPS'))
    has_hash = hash_verifier(expected)[0] is not None
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url.split('#')[0], headers={'User-Agent': 'MITO/0.1'})
    # Salidas de log: se mantienen en español, sin pasar por i18n.
    if has_hash:
        cert_notice = ('Aviso: certificado TLS no verificable; reintentando sin verificarlo '
                       '(integridad garantizada por el hash declarado)')
    else:
        cert_notice = ('Aviso: certificado TLS no verificable; reintentando sin verificarlo '
                       'ni comprobar la integridad (descarga sin hash)')
    insecure = False
    remaining = max(1, attempts)
    pause = max(0.0, delay)
    while True:
        remaining -= 1
        hasher, digest = hash_verifier(expected)
        opener = _build_opener(root, insecure=insecure)
        try:
            with opener.open(request, timeout=60) as response, destination.open('wb') as output:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        if max_bytes == MAX_DOWNLOAD:
                            raise YitoError(tr('Descarga excede el límite de 4 GiB'))
                        raise YitoError(f'Descarga excede el límite de {max_bytes} bytes')
                    if hasher is not None:
                        hasher.update(chunk)
                    output.write(chunk)
        except urllib.error.HTTPError as exc:
            exc.close()
            destination.unlink(missing_ok=True)
            # Errores transitorios de servidor, proxy o gateway: se reintentan.
            # Errores 4xx (salvo 408 y 429) son permanentes y abortan sin reintento.
            if remaining and (exc.code >= 500 or exc.code in (408, 429)):
                print(f'Aviso: HTTP {exc.code}; reintentando la descarga '
                      f'({attempts - remaining + 1}/{max(1, attempts)})', flush=True)
                time.sleep(pause)
                pause *= 2
                continue
            raise
        except urllib.error.URLError as exc:
            destination.unlink(missing_ok=True)
            if isinstance(getattr(exc, 'reason', None), ssl.SSLCertVerificationError) and not insecure:
                insecure = True
                print(cert_notice, flush=True)
                continue
            if remaining:
                print(f'Aviso: error de red ({exc.reason}); reintentando la descarga '
                      f'({attempts - remaining + 1}/{max(1, attempts)})', flush=True)
                time.sleep(pause)
                pause *= 2
                continue
            raise
        except (OSError, http.client.HTTPException) as exc:
            destination.unlink(missing_ok=True)
            # Cortes de conexión, timeouts y lecturas incompletas: se reintentan.
            if remaining:
                print(f'Aviso: conexión interrumpida ({exc}); reintentando la descarga '
                      f'({attempts - remaining + 1}/{max(1, attempts)})', flush=True)
                time.sleep(pause)
                pause *= 2
                continue
            raise
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        else:
            if hasher is not None and hasher.hexdigest().lower() != digest:
                destination.unlink(missing_ok=True)
                raise YitoError(tr('Hash incorrecto; se ha descartado la descarga'))
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
