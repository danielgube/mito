from __future__ import annotations
from .i18n import tr

import ctypes
import hashlib
import io
import os
import struct
import tempfile
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .storage import YitoError


_DEFAULT_BADGE_TEXT = "MITO"
BADGE_COLORS = {
    'negro': (34, 34, 39), 'rojo': (172, 35, 47),
    'azul': (26, 75, 160), 'naranja': (196, 80, 19),
    'rosa': (157, 43, 100), 'verde': (26, 112, 61),
    'marron': (109, 65, 35), 'morado': (102, 54, 135),
}
DEFAULT_BADGE_COLOR = 'naranja'
_BADGE_REVISION = b"mito-shortcut-badge-v5"
_RT_ICON = 3
_RT_GROUP_ICON = 14
_LOAD_LIBRARY_AS_DATAFILE = 0x00000002
_LOAD_LIBRARY_AS_IMAGE_RESOURCE = 0x00000020


def create_mito_shortcut_icon(
    source: Path,
    destination_directory: Path,
    launcher_id: str,
    badge_text: str = _DEFAULT_BADGE_TEXT,
    badge_color: str = DEFAULT_BADGE_COLOR,
) -> Path:
    badge_text = _validate_badge_text(badge_text)
    if badge_color not in BADGE_COLORS:
        raise YitoError(tr('Elige un color de la paleta del icono'))
    source = source.resolve()
    if not source.is_file():
        raise YitoError(tr('No existe el icono del launcher: {p1}', p1=(source)))
    icon_bytes = _read_icon_container(source)
    digest = hashlib.sha256(
        icon_bytes + _BADGE_REVISION + badge_text.encode("utf-8") + badge_color.encode("ascii")
    ).hexdigest()[:16]
    destination_directory.mkdir(parents=True, exist_ok=True)
    destination = (
        destination_directory / f"{launcher_id}-mito-{digest}.ico"
    ).resolve()
    try:
        destination.relative_to(destination_directory.resolve())
    except ValueError as exc:
        raise YitoError(
            tr('El icono generado sale de su directorio: {p1}', p1=(destination))
        ) from exc
    if destination.is_file():
        return destination

    frames = [
        _add_mito_badge(frame, badge_text, BADGE_COLORS[badge_color])
        for frame in _load_icon_frames(icon_bytes, source)
    ]
    generated = _encode_png_ico(frames)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination_directory,
            prefix=f".{launcher_id}-mito-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(generated)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, destination)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise YitoError(
            tr('No se pudo guardar el icono MITO: {p1}', p1=(exc))
        ) from exc
    return destination


def _validate_badge_text(value: str) -> str:
    normalized = value.strip().upper()
    if (
        not 1 <= len(normalized) <= 4
        or not normalized.isalnum()
    ):
        raise YitoError(
            tr('shortcut.badgeText debe contener entre 1 y 4 letras o números')
        )
    return normalized


def _read_icon_container(source: Path) -> bytes:
    if source.suffix.casefold() == ".ico":
        try:
            return source.read_bytes()
        except OSError as exc:
            raise YitoError(
                tr('No se pudo leer el icono {p1}: {p3}', p1=(source), p3=(exc))
            ) from exc
    if source.suffix.casefold() in {".exe", ".dll"}:
        return _extract_first_pe_icon(source)
    raise YitoError(
        tr('Formato de icono no soportado para el acceso directo: {p1}', p1=(source))
    )


def _extract_first_pe_icon(source: Path) -> bytes:
    if os.name != "nt":
        raise YitoError(
            tr('La extracción de iconos de ejecutables requiere Windows')
        )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    load_library = kernel32.LoadLibraryExW
    load_library.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    load_library.restype = wintypes.HMODULE
    free_library = kernel32.FreeLibrary
    free_library.argtypes = [wintypes.HMODULE]
    free_library.restype = wintypes.BOOL
    module = load_library(
        str(source),
        None,
        _LOAD_LIBRARY_AS_DATAFILE | _LOAD_LIBRARY_AS_IMAGE_RESOURCE,
    )
    if not module:
        raise YitoError(
            tr('Windows no pudo abrir los recursos de {p1}: error {p3}', p1=(source), p3=(ctypes.get_last_error()))
        )
    try:
        names = _resource_names(kernel32, module, _RT_GROUP_ICON)
        if not names:
            raise YitoError(
                tr('El ejecutable no contiene un icono extraíble: {p1}', p1=(source))
            )
        group = _resource_bytes(
            kernel32, module, names[0], _RT_GROUP_ICON, source
        )
        return _group_icon_to_ico(kernel32, module, group, source)
    finally:
        free_library(module)


def _resource_names(
    kernel32: ctypes.WinDLL,
    module: int,
    resource_type: int,
) -> list[int | str]:
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMODULE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.LPARAM,
    )
    names: list[int | str] = []

    @callback_type
    def collect(
        _module: int,
        _type_pointer: int,
        name_pointer: int,
        _parameter: int,
    ) -> bool:
        value = int(name_pointer)
        if value <= 0xFFFF:
            names.append(value)
        else:
            names.append(ctypes.wstring_at(value))
        return True

    enumerate_names = kernel32.EnumResourceNamesW
    enumerate_names.argtypes = [
        wintypes.HMODULE,
        ctypes.c_void_p,
        callback_type,
        wintypes.LPARAM,
    ]
    enumerate_names.restype = wintypes.BOOL
    enumerate_names(
        module,
        ctypes.c_void_p(resource_type),
        collect,
        0,
    )
    return names


def _resource_bytes(
    kernel32: ctypes.WinDLL,
    module: int,
    name: int | str,
    resource_type: int,
    source: Path,
) -> bytes:
    name_buffer: ctypes.Array[ctypes.c_wchar] | None = None
    if isinstance(name, int):
        name_pointer = ctypes.c_void_p(name)
    else:
        name_buffer = ctypes.create_unicode_buffer(name)
        name_pointer = ctypes.cast(name_buffer, ctypes.c_void_p)
    find_resource = kernel32.FindResourceW
    find_resource.argtypes = [
        wintypes.HMODULE,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    find_resource.restype = wintypes.HRSRC
    resource = find_resource(
        module,
        name_pointer,
        ctypes.c_void_p(resource_type),
    )
    if not resource:
        raise YitoError(
            tr('Windows no pudo localizar un recurso de icono en {p1}', p1=(source))
        )
    size_resource = kernel32.SizeofResource
    size_resource.argtypes = [wintypes.HMODULE, wintypes.HRSRC]
    size_resource.restype = wintypes.DWORD
    size = int(size_resource(module, resource))
    load_resource = kernel32.LoadResource
    load_resource.argtypes = [wintypes.HMODULE, wintypes.HRSRC]
    load_resource.restype = wintypes.HGLOBAL
    loaded = load_resource(module, resource)
    lock_resource = kernel32.LockResource
    lock_resource.argtypes = [wintypes.HGLOBAL]
    lock_resource.restype = ctypes.c_void_p
    pointer = lock_resource(loaded)
    if not loaded or not pointer or size <= 0:
        raise YitoError(
            tr('Windows no pudo cargar un recurso de icono en {p1}', p1=(source))
        )
    return ctypes.string_at(pointer, size)


def _group_icon_to_ico(
    kernel32: ctypes.WinDLL,
    module: int,
    group: bytes,
    source: Path,
) -> bytes:
    if len(group) < 6:
        raise YitoError(tr('Recurso de icono truncado en {p1}', p1=(source)))
    reserved, image_type, count = struct.unpack_from("<HHH", group)
    if reserved != 0 or image_type != 1 or count < 1:
        raise YitoError(tr('Recurso de icono no válido en {p1}', p1=(source)))
    if len(group) < 6 + (14 * count):
        raise YitoError(tr('Directorio de icono truncado en {p1}', p1=(source)))

    directory_entries: list[bytes] = []
    images: list[bytes] = []
    offset = 6 + (16 * count)
    for index in range(count):
        entry = struct.unpack_from("<BBBBHHIH", group, 6 + (14 * index))
        width, height, colors, entry_reserved = entry[:4]
        planes, bit_count, _declared_size, resource_id = entry[4:]
        image = _resource_bytes(
            kernel32, module, resource_id, _RT_ICON, source
        )
        directory_entries.append(
            struct.pack(
                "<BBBBHHII",
                width,
                height,
                colors,
                entry_reserved,
                planes,
                bit_count,
                len(image),
                offset,
            )
        )
        images.append(image)
        offset += len(image)
    return b"".join(
        [struct.pack("<HHH", 0, 1, count), *directory_entries, *images]
    )


def _load_icon_frames(icon_bytes: bytes, source: Path) -> list[Image.Image]:
    try:
        with Image.open(io.BytesIO(icon_bytes)) as base:
            declared_sizes = base.info.get("sizes")
            sizes = set(declared_sizes or {base.size})
    except (OSError, UnidentifiedImageError) as exc:
        raise YitoError(tr('No se puede interpretar el icono {p1}', p1=(source))) from exc

    supported_sizes = sorted(
        {
            (int(width), int(height))
            for width, height in sizes
            if 0 < int(width) <= 256 and 0 < int(height) <= 256
        },
        key=lambda size: (size[0] * size[1], size),
    )
    if not supported_sizes:
        raise YitoError(tr('El icono no contiene tamaños válidos: {p1}', p1=(source)))

    frames: list[Image.Image] = []
    for size in supported_sizes:
        try:
            with Image.open(io.BytesIO(icon_bytes)) as frame:
                if frame.format == "ICO":
                    frame.size = size
                frame.load()
                frames.append(frame.convert("RGBA"))
        except (OSError, UnidentifiedImageError) as exc:
            raise YitoError(
                tr('No se puede leer el tamaño {p1}x{p3} de {p5}', p1=(size[0]), p3=(size[1]), p5=(source))
            ) from exc
    return frames


def _add_mito_badge(frame: Image.Image, badge_text: str, color=BADGE_COLORS[DEFAULT_BADGE_COLOR]) -> Image.Image:
    frame = frame.convert("RGBA")
    width, height = frame.size
    shortest = min(width, height)
    if shortest <= 48:
        return _add_small_badge(frame, badge_text, color)
    font_size = max(5, round(shortest * 0.23))
    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    while True:
        font = ImageFont.load_default(size=font_size)
        text_box = draw.textbbox((0, 0), badge_text, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        horizontal_padding = max(1, round(shortest * 0.035))
        vertical_padding = max(1, round(shortest * 0.018))
        badge_width = text_width + (2 * horizontal_padding)
        badge_height = text_height + (2 * vertical_padding)
        if badge_width <= width or font_size <= 4:
            break
        font_size -= 1

    left = width - badge_width
    top = height - badge_height
    radius = max(1, round(shortest * 0.04))
    outline_width = max(1, round(shortest * 0.008))
    draw.rounded_rectangle(
        (left, top, width - 1, height - 1),
        radius=radius,
        fill=(*color, 244),
        outline=(255, 255, 255, 245),
        width=outline_width,
    )
    text_x = left + ((badge_width - text_width) / 2) - text_box[0]
    text_y = top + ((badge_height - text_height) / 2) - text_box[1]
    draw.text(
        (text_x, text_y),
        badge_text,
        font=font,
        fill=(255, 255, 255, 255),
    )
    return Image.alpha_composite(frame, overlay)


def _add_small_badge(frame: Image.Image, badge_text: str, color=BADGE_COLORS[DEFAULT_BADGE_COLOR]) -> Image.Image:
    width, height = frame.size
    shortest = min(width, height)
    font_size = max(4, round(shortest * 0.3))
    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    padding = max(1, shortest // 32)

    while True:
        font = ImageFont.load_default(size=font_size)
        text_box = draw.textbbox((0, 0), badge_text, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        badge_width = text_width + (2 * padding)
        badge_height = text_height + (2 * padding)
        if badge_width <= width or font_size <= 4:
            break
        font_size -= 1

    left = width - badge_width
    top = height - badge_height
    draw.rectangle(
        (left, top, width - 1, height - 1),
        fill=(*color, 255),
    )
    text_x = left + ((badge_width - text_width) / 2) - text_box[0]
    text_y = top + ((badge_height - text_height) / 2) - text_box[1]
    draw.fontmode = "1"
    draw.text(
        (text_x, text_y),
        badge_text,
        font=font,
        fill=(255, 255, 255, 255),
    )
    return Image.alpha_composite(frame, overlay)


def _encode_png_ico(frames: list[Image.Image]) -> bytes:
    unique_frames = {frame.size: frame for frame in frames}
    ordered = [
        unique_frames[size]
        for size in sorted(
            unique_frames,
            key=lambda value: (value[0] * value[1], value),
        )
    ]
    if not ordered:
        raise YitoError(tr('No hay imágenes para construir el icono MITO'))

    images: list[bytes] = []
    entries: list[bytes] = []
    offset = 6 + (16 * len(ordered))
    for frame in ordered:
        width, height = frame.size
        buffer = io.BytesIO()
        frame.save(buffer, format="PNG", optimize=True)
        image = buffer.getvalue()
        entries.append(
            struct.pack(
                "<BBBBHHII",
                width if width < 256 else 0,
                height if height < 256 else 0,
                0,
                0,
                1,
                32,
                len(image),
                offset,
            )
        )
        images.append(image)
        offset += len(image)
    return b"".join(
        [struct.pack("<HHH", 0, 1, len(ordered)), *entries, *images]
    )
