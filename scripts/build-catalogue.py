#!/usr/bin/env python3
"""Construit le catalogue mannequins Miraggia (images compressees + mannequins.json).

Relancable : on ajoute / retire un mannequin dans le dossier source, on relance,
les images sont (re)generees uniquement si la source a change et le JSON est a jour.

Usage :
    python scripts/build-catalogue.py                 # source et sortie par defaut
    python scripts/build-catalogue.py --src "D:/CHIMP_ME/MANNEQUINS" --out .
    python scripts/build-catalogue.py --force          # re-encode tout
    python scripts/build-catalogue.py --dry-run        # affiche la classification sans ecrire

Arborescence source attendue (les noms exacts sont decouverts, pas codes en dur) :
    <SRC>/<dossier genre>/<dossier tranche d'age>/<NN.PRENOM>/<images>

    - genre  : dossier dont le nom contient FEMME / WOMEN / HOMME / MEN
    - age    : "20-30 ANS", "4-12 ANS"... (la borne haute <= AGE_ENFANT_MAX => genre "Enfant")
    - prenom : prefixe numerique optionnel ("01.MILA" -> "Mila")

Classification automatique de chaque image (OpenCV, cascades de Haar) :
    - visage petit par rapport a l'image (ou personne entiere detectee) -> plein-pied
    - visage de face detecte                                              -> visage
    - sinon                                                                -> profil
    - aucune personne detectee (ex : photo de fond vide)                   -> ignoree

Corrections manuelles : scripts/catalogue-overrides.json
    { "<id mannequin>": { "<nom de fichier source>": "plein-pied" | "visage" | "profil" | "skip" } }

Sortie :
    <OUT>/mannequins/<genre>/<age>/<prenom>/01-plein-pied.jpg, 02-visage.jpg, 03-profil.jpg ...
                                            + thumb.jpg, thumb-02.jpg, thumb-03.jpg ... (une miniature 3/4 par photo)
    <OUT>/mannequins.json
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import unicodedata
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import cv2
except ImportError:  # pragma: no cover
    sys.exit("OpenCV manquant : pip install opencv-python-headless")

# ---------------------------------------------------------------------------
# Reglages
# ---------------------------------------------------------------------------
REPO = Path(__file__).resolve().parent.parent
DEFAULT_SRC = Path(r"D:/CHIMP_ME/MANNEQUINS")
OVERRIDES_FILE = REPO / "scripts" / "catalogue-overrides.json"
CACHE_FILE = REPO / "scripts" / ".catalogue-cache.json"

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
MAX_WIDTH = 1200
THUMB_WIDTH = 400
THUMB_RATIO = 3 / 4          # largeur / hauteur de la miniature (ratio 3/4 dans la grille)
PHOTO_MAX_KB = 200
THUMB_MAX_KB = 60
JPEG_QUALITY = 80
JPEG_QUALITY_FLOOR = 60

AGE_ENFANT_MAX = 15          # borne haute <= 15 ans => genre "Enfant"
DETECT_HEIGHT = 1000         # hauteur de travail pour la detection
FULL_BODY_FACE_RATIO = 0.16  # visage / hauteur image en dessous => plein pied
FULL_BODY_MAX_WIDTH = 0.55   # largeur du sujet / largeur image : corps entier < 0.45, buste > 0.55

TYPE_ORDER = ["plein-pied", "visage", "profil"]
OUTPUT_FORMAT_VERSION = 2    # a incrementer quand la forme des fichiers de sortie change (invalide le cache)

GENRE_PATTERNS = [
    (re.compile(r"femme|women|woman|female|fille", re.I), "Femme", "femmes"),
    (re.compile(r"homme|men|man|male|garcon|garçon", re.I), "Homme", "hommes"),
]


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------
def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "x"


def pretty_name(folder: str) -> str:
    """'01.MILA' -> 'Mila', '06.JOSÉ' -> 'José', 'jean-luc' -> 'Jean-Luc'."""
    name = re.sub(r"^\s*\d+\s*[.\-_ )]*\s*", "", folder).strip()
    name = name or folder
    return "-".join(part[:1].upper() + part[1:].lower() for part in name.split("-"))


def folder_order(folder: str) -> int:
    m = re.match(r"^\s*(\d+)", folder)
    return int(m.group(1)) if m else 10**6


def parse_age(folder: str) -> tuple[str, int | None, int | None]:
    """'20-30 ANS' -> ('20-30 ans', 20, 30)."""
    nums = [int(n) for n in re.findall(r"\d+", folder)]
    lo = nums[0] if nums else None
    hi = nums[1] if len(nums) > 1 else lo
    label = re.sub(r"\s+", " ", folder).strip()
    label = re.sub(r"\bANS\b", "ans", label, flags=re.I)
    if not re.search(r"ans", label, re.I) and nums:
        label = f"{label} ans"
    return label, lo, hi


def detect_genre(folder: str) -> tuple[str, str] | None:
    for pattern, label, slug in GENRE_PATTERNS:
        if pattern.search(folder):
            return label, slug
    return None


def dedupe_sources(files: list[Path]) -> list[Path]:
    """Si un meme visuel existe en PNG et JPG (meme nom), on garde le PNG (source sans perte)."""
    by_stem: dict[str, Path] = {}
    priority = {".png": 0, ".webp": 1, ".jpg": 2, ".jpeg": 2}
    for f in sorted(files):
        key = f.stem.lower()
        cur = by_stem.get(key)
        if cur is None or priority.get(f.suffix.lower(), 9) < priority.get(cur.suffix.lower(), 9):
            by_stem[key] = f
    return sorted(by_stem.values(), key=lambda p: p.name.lower())


def file_signature(p: Path) -> str:
    st = p.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def load_rgb(path: Path) -> Image.Image:
    im = Image.open(path)
    im.load()
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


# ---------------------------------------------------------------------------
# Classification d'une image
# ---------------------------------------------------------------------------
_cascades: dict[str, cv2.CascadeClassifier] = {}


def cascade(name: str) -> cv2.CascadeClassifier:
    if name not in _cascades:
        _cascades[name] = cv2.CascadeClassifier(cv2.data.haarcascades + name)
    return _cascades[name]


def person_bbox(rgb: np.ndarray) -> dict | None:
    """Boite englobante du sujet (difference avec le fond de studio), en fractions de l'image.

    Retourne {"w": largeur, "h": hauteur, "aspect": hauteur/largeur en pixels} ou None si vide.
    """
    a = rgb.astype(int)
    h, w, _ = a.shape
    border = np.concatenate([a[:5].reshape(-1, 3), a[-5:].reshape(-1, 3),
                             a[:, :5].reshape(-1, 3), a[:, -5:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    mask = np.abs(a - bg).sum(axis=2) > 120
    if mask.sum() < 0.01 * h * w:
        return None
    ys, xs = np.where(mask)
    bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    # Un buste est coupe par le bord bas de l'image ; un corps entier a ses pieds dans le cadre.
    touches_bottom = bool(ys.max() >= h - 4)
    return {"w": round(bw / w, 3), "h": round(bh / h, 3), "aspect": round(bh / bw, 2), "cut": touches_bottom}


def classify_image(path: Path) -> tuple[str, dict]:
    """Retourne (type, details) avec type dans plein-pied / visage / profil / skip.

    Regles (photos de studio, fond uni) :
      1. Aucun sujet (fond vide) => skip.
      2. Sujet entier dans le cadre (ne touche pas le bord bas) et etroit => plein-pied.
         Un visage de face tres petit par rapport a l'image confirme aussi un plein-pied.
      3. Sinon : visage de face detecte (cascade frontale, minNeighbors eleve, sans egalisation
         d'histogramme qui cree de faux visages sur les torses) => visage ; sinon => profil.
    Le detail "weight" (confiance de la cascade frontale) sert ensuite a departager, au sein d'un
    mannequin, deux "visage" sans "profil" : le moins franc (vue de trois-quarts) devient le profil.
    """
    im = load_rgb(path)
    scale = DETECT_HEIGHT / im.height
    small = im.resize((max(1, round(im.width * scale)), DETECT_HEIGHT), Image.BILINEAR)
    rgb = np.asarray(small)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape

    def plausible(f) -> bool:
        # Dans la moitie haute, pas trop excentre (les faux positifs sont plus bas, sur le corps).
        return (f[1] + f[3] / 2) < 0.6 * h and 0.1 * w < (f[0] + f[2] / 2) < 0.9 * w

    rects, _, weights = cascade("haarcascade_frontalface_default.xml").detectMultiScale3(
        gray, scaleFactor=1.08, minNeighbors=8, minSize=(30, 30), outputRejectLevels=True)
    frontal = [(tuple(int(v) for v in r), float(lw)) for r, lw in zip(rects, weights) if plausible(r)]
    alt = cascade("haarcascade_frontalface_alt2.xml").detectMultiScale(gray, scaleFactor=1.08, minNeighbors=8, minSize=(30, 30))
    frontal += [(tuple(int(v) for v in r), 0.0) for r in alt if plausible(r)]

    top_face = min(frontal, key=lambda f: f[0][1])[0] if frontal else None
    face_ratio = top_face[3] / h if top_face else 0.0
    weight = max((lw for _, lw in frontal), default=0.0)
    box = person_bbox(rgb)
    details = {"face": round(face_ratio, 3), "weight": round(weight, 2), "bbox": box}

    if box is None or box["h"] < 0.5:
        return "skip", details
    if not box["cut"] and (box["w"] < FULL_BODY_MAX_WIDTH or (top_face and face_ratio < FULL_BODY_FACE_RATIO)):
        return "plein-pied", details
    # Sujet coupe par le cadre = buste. Un "visage" minuscule sur un buste est un faux positif
    # (oreille, cheveux) : on l'ignore, ce qui laisse le profil.
    if top_face and face_ratio >= FULL_BODY_FACE_RATIO:
        return "visage", details
    return "profil", details


# ---------------------------------------------------------------------------
# Encodage
# ---------------------------------------------------------------------------
def save_jpeg(im: Image.Image, dst: Path, max_kb: int) -> int:
    q = JPEG_QUALITY
    while True:
        im.save(dst, "JPEG", quality=q, progressive=True, optimize=True, subsampling=2 if q < JPEG_QUALITY else 1)
        size = dst.stat().st_size
        if size <= max_kb * 1024 or q <= JPEG_QUALITY_FLOOR:
            return size
        q -= 5


def encode_photo(src: Path, dst: Path) -> int:
    im = load_rgb(src)
    if im.width > MAX_WIDTH:
        im = im.resize((MAX_WIDTH, round(im.height * MAX_WIDTH / im.width)), Image.LANCZOS)
    return save_jpeg(im, dst, PHOTO_MAX_KB)


def encode_thumb(src: Path, dst: Path) -> int:
    """Miniature 3/4 : la photo plein pied entiere, centree sur un fond de la couleur du decor."""
    im = load_rgb(src)
    tw, th = THUMB_WIDTH, round(THUMB_WIDTH / THUMB_RATIO)
    a = np.asarray(im.resize((60, 100)))
    border = np.concatenate([a[:3].reshape(-1, 3), a[-3:].reshape(-1, 3), a[:, :3].reshape(-1, 3), a[:, -3:].reshape(-1, 3)])
    bg = tuple(int(v) for v in np.median(border, axis=0))
    scale = min(tw / im.width, th / im.height)
    inner = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGB", (tw, th), bg)
    canvas.paste(inner, ((tw - inner.width) // 2, (th - inner.height) // 2))
    return save_jpeg(canvas, dst, THUMB_MAX_KB)


# ---------------------------------------------------------------------------
# Traitement d'un mannequin (execute dans un process worker)
# ---------------------------------------------------------------------------
def process_model(job: dict) -> dict:
    src_dir = Path(job["src_dir"])
    out_dir = Path(job["out_dir"])
    overrides: dict[str, str] = job["overrides"]
    dry_run: bool = job["dry_run"]

    files = dedupe_sources([p for p in src_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT])
    classified: list[dict] = []
    warnings: list[str] = []
    for f in files:
        forced = overrides.get(f.name)
        if forced:
            kind, details = forced, {"override": True}
        else:
            try:
                kind, details = classify_image(f)
            except Exception as exc:  # image illisible
                warnings.append(f"{f.name}: illisible ({exc})")
                continue
        if kind == "skip":
            warnings.append(f"{f.name}: aucune personne detectee, ignoree")
            continue
        with Image.open(f) as im:
            pixels = im.width * im.height
        classified.append({"src": f, "kind": kind, "pixels": pixels, "details": details})

    # Deux "visage" et aucun "profil" : la vue de trois-quarts (visage de face le moins franc) devient le profil.
    visages = [c for c in classified if c["kind"] == "visage" and not c["details"].get("override")]
    if len(visages) >= 2 and not any(c["kind"] == "profil" for c in classified):
        weakest = min(visages, key=lambda c: c["details"].get("weight", 0.0))
        weakest["kind"] = "profil"

    # Ordre : 1 plein-pied, 1 visage, 1 profil (le visage le plus franc, sinon la plus grande resolution),
    # puis les extras.
    ordered: list[dict] = []
    remaining = sorted(classified, key=lambda c: (-c["details"].get("weight", 0.0), -c["pixels"]))
    for kind in TYPE_ORDER:
        for c in remaining:
            if c["kind"] == kind:
                ordered.append(c)
                remaining.remove(c)
                break
    for kind in TYPE_ORDER:
        ordered += [c for c in remaining if c["kind"] == kind]
    for kind in TYPE_ORDER:
        if not any(c["kind"] == kind for c in ordered):
            warnings.append(f"pas de photo '{kind}'")

    photos: list[dict] = []
    counters: dict[str, int] = {}
    for i, c in enumerate(ordered, start=1):
        counters[c["kind"]] = counters.get(c["kind"], 0) + 1
        suffix = "" if counters[c["kind"]] == 1 else f"-{counters[c['kind']]}"
        name = f"{i:02d}-{c['kind']}{suffix}.jpg"
        photos.append({"file": name, "type": c["kind"], "source": c["src"].name,
                       "signature": file_signature(c["src"]), "src_path": str(c["src"])})

    # Une miniature 3/4 par photo (carrousel dans la grille) ; la premiere (plein pied) s'appelle thumb.jpg.
    for i, p in enumerate(photos):
        p["thumb"] = "thumb.jpg" if i == 0 else f"thumb-{i + 1:02d}.jpg"

    result = {"id": job["id"], "photos": photos, "warnings": warnings, "sizes": {}}
    if dry_run or not photos:
        return result

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    for p in photos:
        result["sizes"][p["file"]] = encode_photo(Path(p["src_path"]), out_dir / p["file"])
        result["sizes"][p["thumb"]] = encode_thumb(Path(p["src_path"]), out_dir / p["thumb"])
    return result


# ---------------------------------------------------------------------------
# Decouverte de l'arborescence
# ---------------------------------------------------------------------------
def discover(src: Path) -> list[dict]:
    models: list[dict] = []
    for genre_dir in sorted(p for p in src.iterdir() if p.is_dir()):
        g = detect_genre(genre_dir.name)
        if not g:
            print(f"  ! dossier ignore (genre inconnu) : {genre_dir.name}")
            continue
        sexe, sexe_slug = g
        for age_dir in sorted(p for p in genre_dir.iterdir() if p.is_dir()):
            age_label, lo, hi = parse_age(age_dir.name)
            enfant = hi is not None and hi <= AGE_ENFANT_MAX
            age_slug = slugify(age_label)
            for model_dir in sorted((p for p in age_dir.iterdir() if p.is_dir()),
                                    key=lambda p: (folder_order(p.name), p.name.lower())):
                if not any(f.suffix.lower() in IMAGE_EXT for f in model_dir.iterdir() if f.is_file()):
                    continue
                prenom = pretty_name(model_dir.name)
                prenom_slug = slugify(prenom)
                models.append({
                    "id": f"{sexe_slug}-{age_slug}-{prenom_slug}",
                    "prenom": prenom,
                    "genre": "Enfant" if enfant else sexe,
                    "sexe": sexe,
                    "age": age_label,
                    "age_min": lo,
                    "age_max": hi,
                    "order": folder_order(model_dir.name),
                    "rel_dir": f"mannequins/{sexe_slug}/{age_slug}/{prenom_slug}",
                    "src_dir": str(model_dir),
                })
    return models


def model_signature(src_dir: Path) -> str:
    files = sorted(p for p in src_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT)
    return "|".join(f"{p.name}={file_signature(p)}" for p in files)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help="dossier source des mannequins")
    ap.add_argument("--out", type=Path, default=REPO, help="racine du repo (sortie mannequins/ + mannequins.json)")
    ap.add_argument("--force", action="store_true", help="re-encoder toutes les images")
    ap.add_argument("--dry-run", action="store_true", help="classifier seulement, sans ecrire")
    ap.add_argument("--workers", type=int, default=None, help="nombre de process paralleles")
    args = ap.parse_args()

    src: Path = args.src
    out_root: Path = args.out
    if not src.is_dir():
        print(f"Source introuvable : {src}")
        return 1

    overrides: dict = json.loads(OVERRIDES_FILE.read_text("utf-8")) if OVERRIDES_FILE.exists() else {}
    cache: dict = {}
    if CACHE_FILE.exists() and not args.force:
        try:
            cache = json.loads(CACHE_FILE.read_text("utf-8"))
        except json.JSONDecodeError:
            cache = {}

    print(f"Source : {src}")
    models = discover(src)
    print(f"{len(models)} mannequins trouves")

    jobs, reused = [], 0
    for m in models:
        sig = (model_signature(Path(m["src_dir"])) + "|ov=" + json.dumps(overrides.get(m["id"], {}), sort_keys=True)
               + f"|fmt={OUTPUT_FORMAT_VERSION}")
        m["signature"] = sig
        cached = cache.get(m["id"])
        out_dir = out_root / m["rel_dir"]
        if (not args.force and not args.dry_run and cached and cached.get("signature") == sig
                and all((out_dir / p["file"]).exists() and (out_dir / p.get("thumb", "")).is_file() for p in cached["photos"])):
            m["result"] = cached
            reused += 1
        else:
            jobs.append({"id": m["id"], "src_dir": m["src_dir"], "out_dir": str(out_dir),
                         "overrides": overrides.get(m["id"], {}), "dry_run": args.dry_run})
    print(f"{reused} inchanges, {len(jobs)} a traiter")

    results: dict[str, dict] = {}
    if jobs:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(process_model, j): j["id"] for j in jobs}
            for n, fut in enumerate(as_completed(futures), start=1):
                r = fut.result()
                results[r["id"]] = r
                kinds = ", ".join(p["file"] for p in r["photos"]) or "AUCUNE PHOTO"
                print(f"  [{n}/{len(jobs)}] {r['id']}: {kinds}")
                for w in r["warnings"]:
                    print(f"      ! {w}")

    # Assemblage du JSON
    entries, new_cache = [], {}
    for m in models:
        r = m.get("result") or results.get(m["id"])
        if not r or not r["photos"]:
            print(f"  ! {m['id']} : aucune photo exploitable, exclu du catalogue")
            continue
        entry = {
            "id": m["id"],
            "prenom": m["prenom"],
            "genre": m["genre"],
            "sexe": m["sexe"],
            "age": m["age"],
            "ageMin": m["age_min"],
            "ageMax": m["age_max"],
            "thumb": f"{m['rel_dir']}/{r['photos'][0]['thumb']}",
            "photos": [f"{m['rel_dir']}/{p['file']}" for p in r["photos"]],
            "thumbs": [f"{m['rel_dir']}/{p['thumb']}" for p in r["photos"]],
            "types": [p["type"] for p in r["photos"]],
        }
        entries.append(entry)
        new_cache[m["id"]] = {"signature": m["signature"], "photos": [{k: p[k] for k in ("file", "thumb", "type", "source")} for p in r["photos"]],
                              "warnings": r["warnings"]}

    genre_order = {"Femme": 0, "Homme": 1, "Enfant": 2}
    entries.sort(key=lambda e: (genre_order.get(e["genre"], 9), e["ageMin"] or 0, e["age"], e["prenom"].lower()))
    ages = sorted({e["age"] for e in entries}, key=lambda a: (int(re.findall(r"\d+", a)[0]) if re.findall(r"\d+", a) else 999, a))
    genres = [g for g in ("Femme", "Homme", "Enfant") if any(e["genre"] == g for e in entries)]
    catalogue = {
        "generated": date.today().isoformat(),
        "count": len(entries),
        "filters": {"genre": genres, "age": ages},
        "mannequins": entries,
    }

    if args.dry_run:
        print(json.dumps(catalogue, ensure_ascii=False, indent=2))
        return 0

    # Nettoyage des dossiers de sortie orphelins (mannequins retires de la source)
    mann_root = out_root / "mannequins"
    keep = {out_root / e["photos"][0] for e in entries}
    keep_dirs = {p.parent for p in keep}
    if mann_root.exists():
        for d in sorted(mann_root.glob("*/*/*")):
            if d.is_dir() and d not in keep_dirs:
                shutil.rmtree(d)
                print(f"  - supprime (plus dans la source) : {d.relative_to(out_root)}")
        for d in sorted(mann_root.glob("*/*")) + sorted(mann_root.glob("*")):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()

    (out_root / "mannequins.json").write_text(json.dumps(catalogue, ensure_ascii=False, indent=2) + "\n", "utf-8")
    CACHE_FILE.write_text(json.dumps(new_cache, ensure_ascii=False, indent=2), "utf-8")

    # Bilan tailles
    photos_kb = [p.stat().st_size / 1024 for p in mann_root.rglob("0*.jpg")]
    thumbs_kb = [p.stat().st_size / 1024 for p in mann_root.rglob("thumb*.jpg")]
    total_mb = sum(p.stat().st_size for p in mann_root.rglob("*.jpg")) / 1024 / 1024
    print(f"\n{len(entries)} mannequins -> {out_root / 'mannequins.json'}")
    if photos_kb:
        print(f"photos : {len(photos_kb)} fichiers, max {max(photos_kb):.0f} Ko, moyenne {sum(photos_kb)/len(photos_kb):.0f} Ko"
              f" ({sum(1 for k in photos_kb if k > PHOTO_MAX_KB)} au-dessus de {PHOTO_MAX_KB} Ko)")
    if thumbs_kb:
        print(f"thumbs : {len(thumbs_kb)} fichiers, max {max(thumbs_kb):.0f} Ko"
              f" ({sum(1 for k in thumbs_kb if k > THUMB_MAX_KB)} au-dessus de {THUMB_MAX_KB} Ko)")
    print(f"total  : {total_mb:.1f} Mo")
    warned = [(e["id"], new_cache[e["id"]]["warnings"]) for e in entries if new_cache[e["id"]]["warnings"]]
    if warned:
        print("\nA verifier :")
        for mid, ws in warned:
            for w in ws:
                print(f"  {mid}: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
