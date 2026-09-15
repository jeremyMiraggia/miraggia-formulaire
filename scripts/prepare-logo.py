#!/usr/bin/env python3
"""Prepare le logo Miraggia pour le header sombre du catalogue.

- Charge un PNG source (n'importe quelle variante de couleur).
- Si le PNG n'a pas de transparence (fond noir), les pixels sombres
  (R, G et B < 40) deviennent transparents.
- Recolore tous les pixels visibles en "neige" (#EDEDED) en conservant l'alpha,
  rogne les marges transparentes et reduit la hauteur a 240 px (x4 de l'affichage
  a ~50-60 px, suffisant pour les ecrans Retina).

Usage :
    python scripts/prepare-logo.py [SOURCE.png] [DESTINATION.png]

Par defaut : D:/DesktopD/Miraggia/Website/LOGO/LOGO_ABYSSE/LOGO_COMPLET_CENTRE_ABYSSE.png
          -> <repo>/LOGO_COMPLET_CENTRE_NEIGE.png
"""
import sys
from pathlib import Path

from PIL import Image

NEIGE = (0xED, 0xED, 0xED)
DARK_THRESHOLD = 40
MAX_HEIGHT = 240

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SRC = Path(r"D:/DesktopD/Miraggia/Website/LOGO/LOGO_ABYSSE/LOGO_COMPLET_CENTRE_ABYSSE.png")
DEFAULT_DST = REPO / "LOGO_COMPLET_CENTRE_NEIGE.png"


def prepare(src: Path, dst: Path) -> None:
    im = Image.open(src).convert("RGBA")
    px = im.load()
    w, h = im.size

    # Le PNG a-t-il deja une vraie transparence ?
    alpha_min = im.getchannel("A").getextrema()[0]
    has_alpha = alpha_min < 255

    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if not has_alpha and r < DARK_THRESHOLD and g < DARK_THRESHOLD and b < DARK_THRESHOLD:
                px[x, y] = (0, 0, 0, 0)
            elif a > 0:
                px[x, y] = (*NEIGE, a)

    bbox = im.getchannel("A").getbbox()
    if bbox:
        im = im.crop(bbox)
    if im.height > MAX_HEIGHT:
        ratio = MAX_HEIGHT / im.height
        im = im.resize((round(im.width * ratio), MAX_HEIGHT), Image.LANCZOS)

    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, "PNG", optimize=True)
    print(f"{src.name} -> {dst} ({im.width}x{im.height}, {dst.stat().st_size // 1024} Ko)")


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DST
    if not src.exists():
        sys.exit(f"Source introuvable : {src}")
    prepare(src, dst)
