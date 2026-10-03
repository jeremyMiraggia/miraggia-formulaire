#!/usr/bin/env python3
"""Construit le catalogue mannequins Miraggia (images compressees + mannequins.json).

Relancable : on ajoute / retire un mannequin dans le dossier source, on relance,
les images sont (re)generees uniquement si la source a change et le JSON est a jour.

Usage :
    python scripts/build-catalogue.py                 # source et sortie par defaut
    python scripts/build-catalogue.py --src "D:/CHIMP_ME/MANNEQUINS" --out .
    python scripts/build-catalogue.py --force          # re-encode tout
    python scripts/build-catalogue.py --dry-run        # affiche la classification sans ecrire

Arborescence source (les noms exacts sont decouverts, pas codes en dur) :
    <SRC>/<...dossiers de categorie...>/<NN.PRENOM>/<images>

    La profondeur des dossiers de categorie n'est pas imposee : un mannequin est repere
    au fait que son dossier contient des images, et son genre comme sa categorie sont lus
    sur la chaine de ses dossiers parents (le plus profond l'emporte). Sont donc acceptes :

        TENUES FEMMES 20_30 ANS/1.AMBRE/        genre et age sur le meme dossier
        TENUES _+SIZE/FEMMES/3.LOLA/            categorie puis genre
        TENUES ENFANTS/BEBES/1.MILAN/           chapeau puis categorie enfant
        FEMMES/20-30 ANS/01.MILA/               genre puis age (ancienne arborescence)

    - genre  : dossier dont le nom contient FEMME / WOMEN / FILLE / HOMME / MEN / GARCON
    - age    : "20-30 ANS", "4-12 ANS"... (la borne haute <= AGE_ENFANT_MAX => genre "Enfant") ;
               un dossier sans chiffres est une categorie a part ("+SIZE" -> "Plus size",
               "BEBES" -> "Bebe", "TENUES FILLES" -> "Fille", "TENUES GARCONS" -> "Garcon",
               ces trois dernieres etant des categories du genre "Enfant")
    - prenom : prefixe numerique optionnel ("01.MILA" -> "Mila")
    - images : si elles sont numerotees (01.png, 02.png...), cet ordre est conserve tel quel

Classification automatique de chaque image, pour son etiquette (OpenCV, cascades de Haar) :
    - visage petit par rapport a l'image (ou personne entiere detectee) -> plein-pied
    - visage de face detecte                                              -> visage
    - sinon                                                                -> profil
    - aucune personne detectee (ex : photo de fond vide)                   -> ignoree

Corrections manuelles : scripts/catalogue-overrides.json
    { "<id mannequin>": { "<nom de fichier source>": "plein-pied" | "visage" | "mi-corps" | "profil" | "skip" },
      "_par_numero": { "<prefixe d'id ou *>": { "1": "visage", "2": "mi-corps", "3": "plein-pied" } } }
    La regle "_par_numero" impose le type selon le numero de la photo (pas de detection), par categorie.

Sortie :
    <OUT>/mannequins/<genre ou "enfants">/<categorie>/<prenom>/01-plein-pied.jpg, 02-visage.jpg, 03-profil.jpg ...
                                            + thumb.jpg, thumb-02.jpg, thumb-03.jpg ... (une miniature 3/4 par photo)
    <OUT>/mannequins.json
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
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
DEFAULT_SRC = Path(r"H:/Drive partagés/MIRAGGIA SAS/CATALOGUE/CATALOGUE 2026/POUR JEREM")
OVERRIDES_FILE = REPO / "scripts" / "catalogue-overrides.json"
CACHE_FILE = REPO / "scripts" / ".catalogue-cache.json"

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
MAX_WIDTH = 1200
THUMB_WIDTH = 400
PHOTO_MAX_KB = 200
THUMB_MAX_KB = 60
JPEG_QUALITY = 80
JPEG_QUALITY_FLOOR = 60

READ_RETRIES = 4             # lectures d'une meme image avant d'abandonner
READ_RETRY_DELAY = 0.6       # secondes entre deux tentatives (x2 a chaque essai)

AGE_ENFANT_MAX = 15          # borne haute <= 15 ans => genre "Enfant"
DETECT_HEIGHT = 1000         # hauteur de travail pour la detection
FULL_BODY_FACE_RATIO = 0.16  # visage / hauteur image en dessous => plein pied
FULL_BODY_MAX_WIDTH = 0.55   # largeur du sujet / largeur image : corps entier < 0.45, buste > 0.55

TYPE_ORDER = ["plein-pied", "visage", "mi-corps", "profil"]
OUTPUT_FORMAT_VERSION = 3    # a incrementer quand la forme des fichiers de sortie change (invalide le cache)

GENRE_PATTERNS = [
    (re.compile(r"femme|women|woman|female|fille", re.I), "Femme", "femmes"),
    (re.compile(r"homme|men|man|male|garcon|garçon", re.I), "Homme", "hommes"),
]

# Mots de genre a retirer du nom d'un dossier avant d'y lire une tranche d'age
# ("TENUES FEMMES 20_30 ANS" -> "20-30 ans").
GENRE_WORDS = re.compile(r"\b(femmes?|hommes?|women|woman|men|man|females?|males?|filles?|gar[cç]ons?)\b", re.I)

# Prefixe decoratif des dossiers source ("TENUES FEMMES ..." -> "FEMMES ...").
SOURCE_PREFIX = re.compile(r"^\s*tenues?\b", re.I)

CHILD_ROOT = re.compile(r"\benfants?\b|\bkids?\b", re.I)
PLUS_SIZE = re.compile(r"\+\s*size|\bplus\s*size\b", re.I)

# Categories enfant sans tranche d'age chiffree, dans l'ordre d'affichage voulu.
CHILD_CATEGORIES = [
    (re.compile(r"b[ée]b[ée]s?|baby|babies", re.I), "Bébé"),
    (re.compile(r"\bfilles?\b|\bgirls?\b", re.I), "Fille"),
    (re.compile(r"\bgar[cç]ons?\b|\bboys?\b", re.I), "Garçon"),
]


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------
def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "x"


def pretty_name(folder: str) -> str:
    """'01.MILA' -> 'Mila', '06.JOSÉ' -> 'José', '33.1 ALYA' -> 'Alya', 'jean-luc' -> 'Jean-Luc'."""
    name = re.sub(r"^\s*\d+(?:[.,]\d+)*\s*[.\-_ )]*\s*", "", folder).strip()
    name = name or folder
    return "-".join(part[:1].upper() + part[1:].lower() for part in name.split("-"))


def file_number(stem: str) -> int | None:
    """Numero d'ordre d'une photo : en tete ("01.png", "002 copie.png") ou en fin de nom ("AMBRE-1.jpg", "ELYSE2.jpg")."""
    m = re.match(r"^\s*(\d+)", stem) or re.search(r"(\d+)\s*$", stem)
    return int(m.group(1)) if m else None


def number_types_for(model_id: str, overrides: dict) -> dict[str, str]:
    """Regle "_par_numero" des overrides : type impose selon le numero de la photo, par prefixe d'id.

    Exemple : {"_par_numero": {"femmes-20-30-ans": {"1": "visage", "2": "mi-corps", "3": "plein-pied"}}}
    La cle "*" s'applique a tous les mannequins ; le prefixe le plus long l'emporte.
    """
    rules = overrides.get("_par_numero") or {}
    best: dict[str, str] = {}
    best_len = -1
    for prefix, mapping in rules.items():
        if prefix == "*" or model_id.startswith(prefix):
            length = 0 if prefix == "*" else len(prefix)
            if length > best_len:
                best, best_len = {str(k): v for k, v in mapping.items()}, length
    return best


def folder_order(folder: str) -> float:
    """Prefixe numerique du dossier ('01.', '33.1 ' -> 33.1) ; les dossiers sans prefixe passent en dernier."""
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)(?![.,]?\d)", folder)
    if not m:
        return 10**6
    return float(m.group(1).replace(",", "."))


def parse_age(folder: str) -> tuple[str, int | None, int | None]:
    """'20-30 ANS' -> ('20-30 ans', 20, 30)."""
    nums = [int(n) for n in re.findall(r"\d+", folder)]
    lo = nums[0] if nums else None
    hi = nums[1] if len(nums) > 1 else lo
    label = re.sub(r"\s+", " ", folder).strip()
    if not nums:
        # Categorie sans age, ex. "+SIZE" -> "Plus size"
        label = re.sub(r"^\+\s*", "Plus ", label)
        return label[:1].upper() + label[1:].lower(), None, None
    label = re.sub(r"\bANS\b", "ans", label, flags=re.I)
    if not re.search(r"ans", label, re.I) and nums:
        label = f"{label} ans"
    return label, lo, hi


def normalize_category(text: str) -> str:
    """'20_30 ANS' -> '20-30 ans', '40_50ANS' -> '40-50 ans'."""
    t = re.sub(r"[_/]+", "-", text)
    t = re.sub(r"(\d)\s*ans\b", r"\1 ans", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" -.")


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
    """Ouvre l'image, en reessayant : sur un lecteur reseau (Google Drive, OneDrive) une
    lecture echoue parfois alors que le fichier est sain (OSError / [Errno 22])."""
    delay = READ_RETRY_DELAY
    for attempt in range(1, READ_RETRIES + 1):
        try:
            im = Image.open(path)
            im.load()
            break
        except OSError:
            if attempt == READ_RETRIES:
                raise
            time.sleep(delay)
            delay *= 2
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
    """Miniature de THUMB_WIDTH px de large, au ratio d'origine de la photo (pas de bandes ajoutees).

    La grille de la page affiche les vignettes en 2:3 (ratio des portraits sources) en recadrant
    legerement les rares photos d'un autre ratio, cale sur le haut de l'image.
    """
    im = load_rgb(src)
    if im.width > THUMB_WIDTH:
        im = im.resize((THUMB_WIDTH, max(1, round(im.height * THUMB_WIDTH / im.width))), Image.LANCZOS)
    return save_jpeg(im, dst, THUMB_MAX_KB)


# ---------------------------------------------------------------------------
# Traitement d'un mannequin (execute dans un process worker)
# ---------------------------------------------------------------------------
def process_model(job: dict) -> dict:
    src_dir = Path(job["src_dir"])
    out_dir = Path(job["out_dir"])
    overrides: dict[str, str] = job["overrides"]
    dry_run: bool = job["dry_run"]

    number_types: dict[str, str] = job.get("number_types") or {}

    files = dedupe_sources([p for p in src_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT])
    classified: list[dict] = []
    warnings: list[str] = []
    for f in files:
        forced = overrides.get(f.name)
        if not forced and number_types:
            num = file_number(f.stem)
            if num is not None:
                forced = number_types.get(str(num))
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
        try:
            with Image.open(f) as im:
                pixels = im.width * im.height
        except OSError as exc:
            warnings.append(f"{f.name}: illisible ({exc})")
            continue
        classified.append({"src": f, "kind": kind, "pixels": pixels, "details": details})

    def leading_number(c: dict) -> int | None:
        return file_number(c["src"].stem)

    numbered = any(leading_number(c) is not None for c in classified)

    # Deux "visage" et aucun "profil" => la vue de trois-quarts (visage de face le moins franc) devient
    # le profil. Desactive quand le mannequin a des corrections manuelles (etiquettes deja arbitrees).
    visages = [c for c in classified if c["kind"] == "visage" and not c["details"].get("override")]
    if not overrides and len(visages) >= 2 and not any(c["kind"] == "profil" for c in classified):
        weakest = min(visages, key=lambda c: c["details"].get("weight", 0.0))
        weakest["kind"] = "profil"

    ordered: list[dict] = []
    if numbered:
        # Fichiers numerotes (01.png, 02.png...) : l'ordre des numeros fait foi, les non numerotes en dernier.
        ordered = sorted(classified, key=lambda c: (leading_number(c) is None, leading_number(c) or 0, c["src"].name.lower()))
    else:
        # Sinon : 1 plein-pied, 1 visage, 1 profil (le visage le plus franc, sinon la plus grande resolution),
        # puis les extras.
        remaining = sorted(classified, key=lambda c: (-c["details"].get("weight", 0.0), -c["pixels"]))
        for kind in TYPE_ORDER:
            for c in remaining:
                if c["kind"] == kind:
                    ordered.append(c)
                    remaining.remove(c)
                    break
        for kind in TYPE_ORDER:
            ordered += [c for c in remaining if c["kind"] == kind]
    if len(ordered) < 2:
        warnings.append(f"seulement {len(ordered)} photo(s)")

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
    encoded: list[dict] = []
    for p in photos:
        try:
            result["sizes"][p["file"]] = encode_photo(Path(p["src_path"]), out_dir / p["file"])
            result["sizes"][p["thumb"]] = encode_thumb(Path(p["src_path"]), out_dir / p["thumb"])
        except OSError as exc:  # illisible a l'encodage : on garde les autres photos
            warnings.append(f"{p['source']}: illisible a l'encodage ({exc})")
            for leftover in (out_dir / p["file"], out_dir / p["thumb"]):
                leftover.unlink(missing_ok=True)
            continue
        encoded.append(p)

    # Renumerotation si une photo est tombee, pour garder 01, 02, 03 sans trou.
    if len(encoded) != len(photos):
        renamed: list[dict] = []
        for i, p in enumerate(encoded, start=1):
            new_file = re.sub(r"^\d{2}", f"{i:02d}", p["file"])
            new_thumb = "thumb.jpg" if i == 1 else f"thumb-{i:02d}.jpg"
            for old_name, new_name in ((p["file"], new_file), (p["thumb"], new_thumb)):
                if old_name != new_name:
                    (out_dir / old_name).replace(out_dir / new_name)
                    result["sizes"][new_name] = result["sizes"].pop(old_name, 0)
            renamed.append({**p, "file": new_file, "thumb": new_thumb})
        encoded = renamed
    result["photos"] = encoded
    return result


# ---------------------------------------------------------------------------
# Decouverte de l'arborescence
# ---------------------------------------------------------------------------
def classify_folder(name: str) -> dict:
    """Ce qu'un dossier de l'arborescence source apprend : sexe, categorie, enfant.

    Accepte aussi bien un dossier qui ne porte que le genre ("FEMMES") ou que la categorie
    ("20-30 ANS", "+SIZE", "BEBES") qu'un dossier qui porte les deux ("TENUES FEMMES 20_30 ANS").
    """
    info = {"sexe": None, "sexe_slug": None, "categorie": None,
            "age_min": None, "age_max": None, "enfant": False}
    clean = SOURCE_PREFIX.sub("", name).strip(" _-")

    g = detect_genre(clean)
    if g:
        info["sexe"], info["sexe_slug"] = g

    for pattern, label in CHILD_CATEGORIES:
        if pattern.search(clean):
            info["categorie"], info["enfant"] = label, True
            return info

    if CHILD_ROOT.search(clean):          # dossier chapeau "ENFANTS", sans categorie propre
        info["enfant"] = True
        return info

    if PLUS_SIZE.search(clean):
        info["categorie"] = "Plus size"
        return info

    rest = normalize_category(GENRE_WORDS.sub(" ", clean))
    if re.search(r"\d", rest):
        label, lo, hi = parse_age(rest)
        info["categorie"], info["age_min"], info["age_max"] = label, lo, hi
        if hi is not None and hi <= AGE_ENFANT_MAX:
            info["enfant"] = True
    return info


def describe(chain: list[str]) -> dict:
    """Fusionne ce qu'apprennent les dossiers parents ; le plus profond l'emporte."""
    acc = {"sexe": None, "sexe_slug": None, "categorie": None,
           "age_min": None, "age_max": None, "enfant": False}
    for name in chain:
        info = classify_folder(name)
        if info["sexe"]:
            acc["sexe"], acc["sexe_slug"] = info["sexe"], info["sexe_slug"]
        if info["categorie"]:
            acc["categorie"] = info["categorie"]
            acc["age_min"], acc["age_max"] = info["age_min"], info["age_max"]
        if info["enfant"]:
            acc["enfant"] = True
    return acc


def model_dirs(src: Path, max_depth: int = 3) -> list[tuple[Path, list[str]]]:
    """Dossiers contenant des images, avec la chaine de dossiers depuis <src> (categories + mannequin).

    La profondeur des categories n'est pas imposee : un mannequin peut etre range sous
    "<genre+age>/" comme sous "<chapeau>/<categorie>/".
    """
    found: list[tuple[Path, list[str]]] = []

    def walk(d: Path, chain: list[str]) -> None:
        entries = list(d.iterdir())
        if chain and any(f.suffix.lower() in IMAGE_EXT for f in entries if f.is_file()):
            found.append((d, chain))      # dossier mannequin : on ne descend pas plus bas
            return
        if len(chain) >= max_depth:
            return
        for sub in sorted((p for p in entries if p.is_dir()),
                          key=lambda p: (folder_order(p.name), p.name.lower())):
            walk(sub, chain + [sub.name])

    walk(src, [])
    return found


def discover(src: Path) -> list[dict]:
    models: list[dict] = []
    seen: dict[str, str] = {}
    for model_dir, chain in model_dirs(src):
        categories, folder = chain[:-1], chain[-1]
        trail = "/".join(chain)
        if not categories:
            print(f"  ! dossier ignore (pas de categorie parente) : {trail}")
            continue
        info = describe(categories)
        if not info["categorie"]:
            print(f"  ! dossier ignore (categorie inconnue) : {trail}")
            continue
        if not info["enfant"] and not info["sexe"]:
            print(f"  ! dossier ignore (genre inconnu) : {trail}")
            continue

        enfant = info["enfant"]
        root_slug = "enfants" if enfant else info["sexe_slug"]
        cat_slug = slugify(info["categorie"])
        prenom = pretty_name(folder)
        model_id = f"{root_slug}-{cat_slug}-{slugify(prenom)}"
        if model_id in seen:
            print(f"  ! doublon ignore : {trail} (meme identifiant que {seen[model_id]})")
            continue
        seen[model_id] = trail

        models.append({
            "id": model_id,
            "prenom": prenom,
            "genre": "Enfant" if enfant else info["sexe"],
            "sexe": info["sexe"],
            "age": info["categorie"],
            "age_min": info["age_min"],
            "age_max": info["age_max"],
            "order": folder_order(folder),
            "rel_dir": f"mannequins/{root_slug}/{cat_slug}/{slugify(prenom)}",
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
        number_types = number_types_for(m["id"], overrides)
        sig = (model_signature(Path(m["src_dir"])) + "|ov=" + json.dumps(overrides.get(m["id"], {}), sort_keys=True)
               + "|num=" + json.dumps(number_types, sort_keys=True) + f"|fmt={OUTPUT_FORMAT_VERSION}")
        m["signature"] = sig
        cached = cache.get(m["id"])
        out_dir = out_root / m["rel_dir"]
        if (not args.force and not args.dry_run and cached and cached.get("signature") == sig
                and all((out_dir / p["file"]).exists() and (out_dir / p.get("thumb", "")).is_file() for p in cached["photos"])):
            m["result"] = cached
            reused += 1
        else:
            jobs.append({"id": m["id"], "src_dir": m["src_dir"], "out_dir": str(out_dir),
                         "overrides": overrides.get(m["id"], {}), "number_types": number_types, "dry_run": args.dry_run})
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
    entries, new_cache, unreadable, excluded = [], {}, [], []
    for m in models:
        r = m.get("result") or results.get(m["id"])
        if not r or not r["photos"]:
            print(f"  ! {m['id']} : aucune photo exploitable, exclu du catalogue")
            excluded.append(m["id"])
            continue
        entry = {
            "id": m["id"],
            "prenom": m["prenom"],
            "genre": m["genre"],
            "sexe": m["sexe"],
            "age": m["age"],
            "ageMin": m["age_min"],
            "ageMax": m["age_max"],
            "ordre": int(m["order"]) if float(m["order"]).is_integer() else m["order"],
            "thumb": f"{m['rel_dir']}/{r['photos'][0]['thumb']}",
            "photos": [f"{m['rel_dir']}/{p['file']}" for p in r["photos"]],
            "thumbs": [f"{m['rel_dir']}/{p['thumb']}" for p in r["photos"]],
            "types": [p["type"] for p in r["photos"]],
        }
        entries.append(entry)
        # Une photo illisible vient presque toujours du lecteur reseau, pas du fichier : on ne met
        # pas ce mannequin en cache, pour qu'une relance le retente au lieu de figer un resultat partiel.
        if any("illisible" in w for w in r["warnings"]):
            unreadable.append(m["id"])
            continue
        new_cache[m["id"]] = {"signature": m["signature"], "photos": [{k: p[k] for k in ("file", "thumb", "type", "source")} for p in r["photos"]],
                              "warnings": r["warnings"]}

    genre_order = {"Femme": 0, "Homme": 1, "Enfant": 2}
    # Ordre du catalogue : genre, puis categorie (ages croissants, puis les categories sans age),
    # puis l'ordre des dossiers source (prefixe numerique "01.", "02."...), jamais l'alphabet.
    entries.sort(key=lambda e: (genre_order.get(e["genre"], 9), e["ageMin"] if e["ageMin"] is not None else 999,
                                e["age"], e["ordre"], e["prenom"].lower()))
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
    all_results = {m["id"]: (m.get("result") or results.get(m["id"])) for m in models}
    warned = [(e["id"], all_results[e["id"]]["warnings"])
              for e in entries if all_results.get(e["id"]) and all_results[e["id"]]["warnings"]]
    if warned:
        print("\nA verifier :")
        for mid, ws in warned:
            for w in ws:
                print(f"  {mid}: {w}")
    if excluded:
        print(f"\n{len(excluded)} mannequin(s) EXCLU(S) du catalogue (aucune photo lisible) :")
        for mid in excluded:
            print(f"  - {mid}")
    if unreadable:
        print(f"\n{len(unreadable)} mannequin(s) avec une photo illisible, non mis en cache :")
        for mid in unreadable:
            print(f"  - {mid}")
    if excluded or unreadable:
        print("\nCes cas viennent presque toujours du lecteur reseau, pas des fichiers.")
        print("Relancer la meme commande : seuls ces mannequins seront retentes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
