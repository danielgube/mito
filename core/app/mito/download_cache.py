"""Caché persistente de las descargas de componentes.

Descargar no cambia instalaciones ni profiles: ocurre fuera del lock de mutación,
con un lock propio por componente, y sobrevive a un cierre brusco de MITO. El
siguiente intento reanuda el archivo parcial si el manifiesto declara hash; sin
hash empieza de nuevo. Publicar toma el lock de mutación y extrae lo descargado.

Cada manifiesto tiene su carpeta (huella del manifiesto): otra versión o un
manifiesto cambiado nunca reutiliza bytes ajenos. Las carpetas de un componente
se borran tras publicarlo o eliminarlo; un fallo al publicar las conserva para
reintentar sin volver a descargar.
"""
from .i18n import tr
import contextlib
import hashlib
import os
import shutil
import threading
from .catalog import array, effective, fingerprint
from .storage import YitoError, identifier, mutation
from .transfer import download, download_hashes, hash_verifier

_guard = threading.Lock()
_locks = {}


def folder(root, key):
    bucket, app = key.split('/')
    return root / 'core/cache/downloads' / identifier(bucket) / identifier(app)


def entry(root, key, manifest):
    return folder(root, key) / fingerprint(manifest)[:16]


def lock_name(key):
    return 'download-' + hashlib.sha1(key.encode('utf-8')).hexdigest()[:16] + '.lock'


def _thread_lock(root, key):
    with _guard:
        return _locks.setdefault((str(root).casefold(), key), threading.Lock())


@contextlib.contextmanager
def exclusive(root, key, log=None, wait=True):
    """Un componente se descarga en un solo hilo y proceso a la vez.

    Dentro de este proceso (cola en segundo plano y primer plano) se espera el turno,
    cancelable; otro proceso de MITO falla en el acto con un aviso. Componentes
    distintos se descargan sin esperarse."""
    from . import operation
    local = _thread_lock(root, key)
    if not local.acquire(blocking=False):
        if not wait:
            raise YitoError(tr('Otro proceso de MITO está descargando componentes. Espera a que termine.'))
        if log is not None:
            log(tr('Esperando a que termine la descarga en curso de {p1}', p1=key))
        while not local.acquire(timeout=0.25):
            operation.checkpoint()
    try:
        stack = contextlib.ExitStack()
        try:
            stack.enter_context(mutation(root, lock_name(key)))
        except YitoError:
            raise YitoError(tr('Otro proceso de MITO está descargando componentes. Espera a que termine.')) from None
        with stack:
            yield
    finally:
        local.release()


def downloaded(root, key, manifest):
    """True si todas las URL del manifiesto están completas en la caché (sin rehacer hashes)."""
    urls = array(effective(manifest).get('url'))
    base = entry(root, key, manifest)
    return bool(urls) and all((base / f'download-{index}').is_file() for index in range(len(urls)))


def verified(path, expected):
    hasher, digest = hash_verifier(expected)
    if hasher is None:
        return True
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            hasher.update(block)
    return hasher.hexdigest().lower() == digest


def artifact(root, key, manifest, index, url, expected, log, announce=lambda: None, fetch=download):
    """Archivo completo de la URL index: reutilizado si sigue siendo válido o descargado ahora.

    announce se llama sólo cuando hay que descargar (fase y mensajes de la UI);
    fetch es transfer.download salvo que el llamador indique otra (pruebas)."""
    target = entry(root, key, manifest) / f'download-{index}'
    partial = target.with_name(target.name + '.partial')
    with exclusive(root, key, log):
        if target.is_file():
            # Re-checked on every use: the cache is outside the lock that publishes.
            if verified(target, expected):
                log(tr('Usando la descarga ya disponible de {p1} ({p2}/{p3})', p1=key, p2=index + 1,
                       p3=len(array(effective(manifest)['url']))))
                return target
            target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        if expected is None:
            log(tr('{p0}: descarga sin hash declarado ({p2}/{p4})', p0=(key), p2=(index + 1),
                   p4=(len(array(effective(manifest)['url'])))))
        elif partial.is_file():
            log(tr('Reanudando la descarga de {p1} ({p2} bytes ya descargados)', p1=key, p2=partial.stat().st_size))
        announce()
        fetch(url, partial, expected, root=root, resume=True)
        os.replace(partial, target)
        return target


def prefetch(root, records, log, announce=lambda key, manifest, index, total: None, fetch=download):
    """Descarga, sin el lock de mutación, todo lo que publicar instalará después."""
    from . import operation
    for key, record in records.items():
        manifest = record['manifest']
        urls = array(effective(manifest)['url'])
        hashes = download_hashes(urls, effective(manifest).get('hash'))
        for index, (url, expected) in enumerate(zip(urls, hashes)):
            artifact(root, key, manifest, index, url, expected, log,
                     lambda key=key, manifest=manifest, index=index, total=len(urls): announce(key, manifest, index, total),
                     fetch)
            operation.checkpoint()


def discard(root, key):
    """Borra lo descargado de un componente; si alguien lo está descargando, lo deja."""
    try:
        with exclusive(root, key, wait=False):
            shutil.rmtree(folder(root, key), ignore_errors=True)
    except YitoError:
        pass
