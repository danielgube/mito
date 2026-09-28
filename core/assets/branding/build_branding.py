"""Construye las variantes de branding de MITO a partir de los maestros PNG.

Lee mito_icon.png y pocketools_icon.png de este mismo directorio y regenera
in situ: los ICO multirresolución (PNG RGBA de 16 a 256 px), la vista previa
de 256 px y las variantes aplanadas sobre degradado verde oscuro (JPG y el
banner del README). Cada maestro se limpia de artefactos, se recorta al
contenido y se escala hasta su margen configurado sobre el contenido sólido
(alpha > 48). Los ICO finales se encuadran sin margen en el eje mayor.

El maestro MITO es un render aprobado por el usuario (octágono verde, trazas
cian-azuladas, chevron «>» naranja) y el de Pocketools muestra una tuerca naranja con «>» azul eléctrico.

Ambos llegan con su paleta final: no se aplica ningún
desplazamiento de color, sólo limpieza, escalado y emisión de variantes. La
función shift_cyan_to_blue queda disponible para futuros retoques.

Uso: python core/assets/branding/build_branding.py --icons-only (sólo Pillow).
Sin argumentos regenera también las variantes promocionales (NumPy y Matplotlib).
"""
from __future__ import annotations

import io
import struct
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BRAND = Path(__file__).resolve().parent
ROOT = BRAND.parents[2]
CHECK_DIR = ROOT / ".scratch" / "branding"
CHECK_DIR.mkdir(parents=True, exist_ok=True)

MASTER_SIZE = 1024
SOLID_MARGIN = 12            # margen del contenido sólido (alpha > SOLID_ALPHA)
SOLID_ALPHA = 48
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]

# Banda cian -> azul eléctrico en las conexiones interiores
BLUE_H0, BLUE_H1 = 155.0, 182.0   # banda de tono cian (grados)
BLUE_SHIFT = 48.0                 # +48°: cian ~168° -> azul eléctrico ~216°

# Fondo verde oscuro para las variantes aplanadas (JPG / banner)
BG_INNER = (14, 58, 34)      # #0E3A22
BG_OUTER = (3, 18, 10)       # #03120A
LIME = (157, 248, 113)       # texto MITO
TAG = (207, 245, 216)        # tagline

FONT_DIRS = [Path(r"C:\Windows\Fonts")]


def find_font(names: list[str]) -> Path | None:
    for d in FONT_DIRS:
        for n in names:
            p = d / n
            if p.is_file():
                return p
    return None


def shift_cyan_to_blue(im: Image.Image) -> Image.Image:
    """Convierte las conexiones cian del interior en azul eléctrico.

    Banda de tono [BLUE_H0, BLUE_H1] con bordes suaves, limitada al interior
    de la insignia (máscara sólida erosionada) para no teñir el borde del
    hexágono. Los verdes lima/esmeralda y el naranja central no se tocan.
    """
    arr = np.asarray(im).astype(np.float64)
    rgb = arr[..., :3] / 255.0
    alpha = arr[..., 3]
    hsv = rgb_to_hsv(rgb)
    h, s, v = hsv[..., 0] * 360.0, hsv[..., 1], hsv[..., 2]

    solid = alpha > SOLID_ALPHA
    # Interior = máscara sólida erosionada: el borde está cerca del exterior
    # transparente; las trazas viven en el interior.
    eroded = (
        Image.fromarray((solid * 255).astype(np.uint8))
        .filter(ImageFilter.MinFilter(161))   # ~80 px de distancia al exterior
        .filter(ImageFilter.GaussianBlur(8))  # transición suave
    )
    w_r = np.asarray(eroded).astype(np.float64) / 255.0

    w_h = np.clip((h - BLUE_H0) / 6.0, 0, 1) * np.clip((BLUE_H1 - h) / 6.0, 0, 1)
    w_sv = np.clip((s - 0.25) / 0.15, 0, 1) * np.clip((v - 0.20) / 0.15, 0, 1)
    w = w_h * w_r * w_sv * (alpha > 8)

    h2 = (h + BLUE_SHIFT * w) % 360.0
    rgb2 = hsv_to_rgb(np.stack([h2 / 360.0, s, v], axis=-1))
    arr[..., :3] = rgb2 * 255.0
    print(f"  azul eléctrico: {int((w > 0.5).sum())} px desplazados ~{int(BLUE_SHIFT)}°")
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def prepare_master(src: Path, dst: Path, *, blue_shift: bool,
                   solid_margin: int = SOLID_MARGIN) -> Image.Image:
    """Limpieza de artefactos y escalado al límite; azul opcional.

    El escalado y el centrado se calculan sobre el contenido sólido
    (alpha > SOLID_ALPHA) con solid_margin de margen; el halo exterior tenue
    puede quedar recortado por el lienzo, lo que es invisible en la práctica.
    blue_shift sólo se aplica a maestros cuya paleta aún no es final (hoy,
    Pocketools); el maestro MITO ya trae su paleta definitiva.
    """
    im = Image.open(src).convert("RGBA")
    if blue_shift:
        im = shift_cyan_to_blue(im)
    arr = np.array(im)
    alpha = arr[..., 3]
    alpha[alpha < 16] = 0

    # Componentes conexos sobre máscara reducida: conserva solo islas grandes
    small = np.array(Image.fromarray(alpha).resize((256, 256), Image.BILINEAR)) > 12
    labels = np.zeros(small.shape, dtype=np.int32)
    current = 0
    areas: dict[int, int] = {}
    for sy in range(256):
        for sx in range(256):
            if small[sy, sx] and labels[sy, sx] == 0:
                current += 1
                q = deque([(sy, sx)])
                labels[sy, sx] = current
                area = 0
                while q:
                    y, x = q.popleft()
                    area += 1
                    for ny, nx in ((y-1, x), (y+1, x), (y, x-1), (y, x+1)):
                        if 0 <= ny < 256 and 0 <= nx < 256 and small[ny, nx] and labels[ny, nx] == 0:
                            labels[ny, nx] = current
                            q.append((ny, nx))
                areas[current] = area
    if not areas:
        raise SystemExit(f"sin contenido visible en {src}")
    biggest = max(areas.values())
    keep = {lbl for lbl, a in areas.items() if a >= max(30, biggest * 0.002)}
    removed = {lbl: a for lbl, a in areas.items() if lbl not in keep}
    if removed:
        print(f"  {src.name}: eliminadas {len(removed)} manchas aisladas")
    keep_mask = np.array(
        Image.fromarray((np.isin(labels, list(keep)) * 255).astype(np.uint8))
        .resize(alpha.shape[::-1], Image.NEAREST)
    ) > 0
    alpha[~keep_mask] = 0
    arr[..., 3] = alpha
    im = Image.fromarray(arr)

    ys, xs = np.where(alpha > 0)
    full = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    solid = alpha > SOLID_ALPHA
    sys_, sxs = np.where(solid)
    sbox = (int(sxs.min()), int(sys_.min()), int(sxs.max()) + 1, int(sys_.max()) + 1)

    im = im.crop(full)
    solid_w, solid_h = sbox[2] - sbox[0], sbox[3] - sbox[1]
    scale = (MASTER_SIZE - 2 * solid_margin) / max(solid_w, solid_h)
    new_size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
    im = im.resize(new_size, Image.LANCZOS)

    # Centrar por el sólido, no por el halo
    scx = (sbox[0] + sbox[2]) / 2 - full[0]
    scy = (sbox[1] + sbox[3]) / 2 - full[1]
    paste_x = round(MASTER_SIZE / 2 - scx * scale)
    paste_y = round(MASTER_SIZE / 2 - scy * scale)
    canvas = Image.new("RGBA", (MASTER_SIZE, MASTER_SIZE), (0, 0, 0, 0))
    canvas.paste(im, (paste_x, paste_y), im)
    canvas.save(dst, optimize=True)
    print(f"  maestro {dst.name}: sólido {solid_w}x{solid_h} escala x{scale:.3f} -> "
          f"{round(solid_w*scale)}x{round(solid_h*scale)} en lienzo {MASTER_SIZE}")
    return canvas


def encode_png_ico(frames: list[Image.Image]) -> bytes:
    """ICO con entradas PNG RGBA (mismo formato que mito.icons._encode_png_ico)."""
    images, entries = [], []
    offset = 6 + 16 * len(frames)
    for frame in frames:
        buf = io.BytesIO()
        frame.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        w, h = frame.size
        entries.append(struct.pack("<BBBBHHII", w if w < 256 else 0, h if h < 256 else 0,
                                   0, 0, 1, 32, len(data), offset))
        images.append(data)
        offset += len(data)
    return b"".join([struct.pack("<HHH", 0, 1, len(frames)), *entries, *images])


def make_ico(master: Image.Image, dst: Path) -> None:
    frames = [master.resize((s, s), Image.LANCZOS) for s in ICO_SIZES]
    dst.write_bytes(encode_png_ico(frames))
    print(f"  {dst.name}: {len(frames)} tamaños {ICO_SIZES}")


def build_icons() -> None:
    """Encuadra ambos ICO al borde sin alterar los maestros ni sus proporciones.

    Sólo necesita Pillow. El recorte usa alpha > 48 para ignorar halos tenues;
    conserva la proporción y centra el eje más corto sin deformar el símbolo.
    """
    for name in ("mito", "pocketools"):
        master = Image.open(BRAND / f"{name}_icon.png").convert("RGBA")
        box = master.getchannel("A").point(lambda a: 255 if a > SOLID_ALPHA else 0).getbbox()
        if box is None:
            raise ValueError(f"Maestro vacío: {name}")
        art = master.crop(box)
        scale = MASTER_SIZE / max(art.size)
        art = art.resize(tuple(round(s * scale) for s in art.size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (MASTER_SIZE, MASTER_SIZE))
        canvas.alpha_composite(art, ((MASTER_SIZE-art.width)//2, (MASTER_SIZE-art.height)//2))
        make_ico(canvas, BRAND / f"{name}.ico")
        canvas.resize((256, 256), Image.Resampling.LANCZOS).save(BRAND / f"{name}_icon_preview.png")


def dark_canvas(w: int, h: int) -> Image.Image:
    """Degradado radial verde oscuro, más luminoso en el centro."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    cx, cy = w / 2, h * 0.42
    dist = np.sqrt(((xx - cx) / (w * 0.75)) ** 2 + ((yy - cy) / (h * 0.9)) ** 2)
    t = np.clip(dist, 0, 1)[..., None]
    inner = np.array(BG_INNER, dtype=np.float64)
    outer = np.array(BG_OUTER, dtype=np.float64)
    rgb = (inner * (1 - t) + outer * t).astype(np.uint8)
    return Image.fromarray(rgb, "RGB")


def halo(base: Image.Image, center: tuple[float, float], radius: float,
         color=(120, 255, 140), peak=90) -> None:
    """Halo luminoso suave detrás del icono."""
    yy, xx = np.mgrid[0:base.height, 0:base.width].astype(np.float64)
    dist = np.sqrt((xx - center[0]) ** 2 + (yy - center[1]) ** 2) / radius
    a = np.clip(1 - dist, 0, 1) ** 2 * peak
    arr = np.zeros((base.height, base.width, 4), dtype=np.uint8)
    arr[..., 0], arr[..., 1], arr[..., 2] = color
    arr[..., 3] = a.astype(np.uint8)
    layer = Image.fromarray(arr, "RGBA").filter(ImageFilter.GaussianBlur(40))
    base.alpha_composite(layer)


def glow_text(base: Image.Image, xy: tuple[float, float], text: str, font,
              fill, glow=(90, 240, 120), anchor="mm", blur=10) -> None:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text(xy, text, font=font, fill=(*glow, 255), anchor=anchor)
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    base.alpha_composite(layer)
    ImageDraw.Draw(base).text(xy, text, font=font, fill=(*fill, 255), anchor=anchor)


def main() -> None:
    global np, hsv_to_rgb, rgb_to_hsv
    import numpy as np
    from matplotlib.colors import hsv_to_rgb, rgb_to_hsv
    bold = find_font(["segoeuib.ttf", "arialbd.ttf"])
    semi = find_font(["seguisb.ttf", "segoeui.ttf", "arial.ttf"])
    print(f"fuentes: bold={bold}, semi={semi}")

    print("Maestro MITO (paleta final, margen 2 px):")
    mito = prepare_master(BRAND / "mito_icon.png", BRAND / "mito_icon.png",
                          blue_shift=False, solid_margin=2)
    print("Maestro Pocketools (paleta final, margen 8 px):")
    pocket = prepare_master(BRAND / "pocketools_icon.png", BRAND / "pocketools_icon.png",
                            blue_shift=False, solid_margin=8)

    make_ico(mito, BRAND / "mito.ico")
    make_ico(pocket, BRAND / "pocketools.ico")

    mito.resize((256, 256), Image.LANCZOS).save(BRAND / "mito_icon_preview.png", optimize=True)
    print("  mito_icon_preview.png 256")

    # --- variantes aplanadas ---
    bg = dark_canvas(1024, 1024).convert("RGBA")
    halo(bg, (512, 512), 470)
    icon = mito.resize((900, 900), Image.LANCZOS)
    bg.alpha_composite(icon, ((1024 - 900) // 2, (1024 - 900) // 2))
    bg.convert("RGB").save(BRAND / "mito_ico.jpg", quality=92)
    print("  mito_ico.jpg")

    # logo cuadrado: icono + MITO + tagline
    sq = dark_canvas(1024, 1024).convert("RGBA")
    halo(sq, (512, 350), 420)
    icon = mito.resize((560, 560), Image.LANCZOS)
    sq.alpha_composite(icon, ((1024 - 560) // 2, 70))
    f_big = ImageFont.truetype(str(bold), 190) if bold else ImageFont.load_default(size=190)
    f_tag = ImageFont.truetype(str(semi), 46) if semi else ImageFont.load_default(size=46)
    glow_text(sq, (512, 800), "MITO", f_big, LIME, blur=14)
    ImageDraw.Draw(sq).text((512, 905), "My Integrated Terminal Orchestrator",
                            font=f_tag, fill=(*TAG, 255), anchor="mm")
    sq.convert("RGB").save(BRAND / "mito_logo.jpg", quality=92)
    print("  mito_logo.jpg")

    # banner README 1407x768: icono a la izquierda, texto a la derecha
    banner = dark_canvas(1407, 768).convert("RGBA")
    halo(banner, (330, 384), 360)
    icon = mito.resize((520, 520), Image.LANCZOS)
    banner.alpha_composite(icon, (70, (768 - 520) // 2))
    f_big = ImageFont.truetype(str(bold), 168) if bold else ImageFont.load_default(size=168)
    f_tag = ImageFont.truetype(str(semi), 42) if semi else ImageFont.load_default(size=42)
    glow_text(banner, (660, 352), "MITO", f_big, LIME, anchor="lm", blur=14)
    ImageDraw.Draw(banner).text((668, 500), "My Integrated Terminal Orchestrator",
                                font=f_tag, fill=(*TAG, 255), anchor="lm")
    banner.convert("RGB").save(BRAND / "mito_logo.png", optimize=True)
    print("  mito_logo.png (banner README)")

    # shortcut: arte amplio sin texto
    wide = dark_canvas(1408, 768).convert("RGBA")
    halo(wide, (704, 384), 380)
    icon = mito.resize((560, 560), Image.LANCZOS)
    wide.alpha_composite(icon, ((1408 - 560) // 2, (768 - 560) // 2))
    wide.convert("RGB").save(BRAND / "mito_shortcut.jpg", quality=92)
    print("  mito_shortcut.jpg")

    # contact sheet fuera del repo: frames pequeños sobre gris para legibilidad
    sheet = Image.new("RGB", (8 + sum(s + 14 for s in ICO_SIZES), 90), (90, 96, 92))
    x = 8
    for s in ICO_SIZES:
        fr = mito.resize((s, s), Image.LANCZOS)
        tile = Image.new("RGBA", (s, s), (90, 96, 92, 255))
        tile.alpha_composite(fr)
        sheet.paste(tile.convert("RGB"), (x, (90 - s) // 2))
        x += s + 14
    sheet.save(CHECK_DIR / "_sheet_small_sizes.png")
    print(f"  {CHECK_DIR / '_sheet_small_sizes.png'}")


if __name__ == "__main__":
    import sys
    if "--icons-only" in sys.argv:
        build_icons()
    else:
        main()
        build_icons()

