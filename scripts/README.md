# Scripts du catalogue mannequins

## Prérequis (une fois)

```bash
pip install pillow numpy "opencv-python-headless<5"
```

OpenCV 4 est requis : la version 5 a retiré les cascades de Haar utilisées pour classer les photos.

## Reconstruire le catalogue

```bash
python scripts/build-catalogue.py
```

- Source par défaut : `D:\CHIMP_ME\MANNEQUINS` (option `--src` pour un autre dossier).
- Sortie : `mannequins/` (images compressées à 1200 px + une miniature 3/4 de 400 px par photo :
  `thumb.jpg`, `thumb-02.jpg`…) et `mannequins.json` à la racine du repo.
- Relançable : seuls les mannequins dont les fichiers source ont changé sont ré-encodés
  (cache dans `scripts/.catalogue-cache.json`). `--force` ré-encode tout.
- `--dry-run` affiche la classification et le JSON sans rien écrire.
- Les dossiers de sortie des mannequins retirés de la source sont supprimés automatiquement.

Arborescence source attendue : `<genre>/<catégorie>/<NN.PRENOM>/<images>`.
Le genre est déduit du nom du dossier (FEMMES / HOMMES), la catégorie de ses chiffres
(`20-30 ANS` → « 20-30 ans », borne haute ≤ 15 ans ⇒ genre `Enfant`) ou de son nom
s'il n'en a pas (`+SIZE` → « Plus size »), le prénom du nom du dossier sans son préfixe numérique.

### Ordre et étiquettes des photos

Si les images sont numérotées (`01.png`, `02.png`…), cet ordre est conservé tel quel.
Chaque image reçoit ensuite une étiquette `visage`, `profil` ou `plein-pied` (détection de
visage OpenCV + silhouette sur fond de studio), utilisée pour nommer les fichiers de sortie
(`01-visage.jpg`, `02-profil.jpg`) et pour l'affichage. Le script signale en fin d'exécution
les cas à vérifier (photo ignorée, mannequin avec une seule photo).

### Corriger un cas à la main

Créer `scripts/catalogue-overrides.json` :

```json
{
  "femmes-20-30-ans-mila": {
    "0055.png": "profil",
    "photo-inutile.jpg": "skip"
  }
}
```

Clé = `id` du mannequin dans `mannequins.json`, valeurs = `plein-pied`, `visage`, `profil` ou `skip`.
Relancer le script : les mannequins concernés sont ré-encodés.

## Logo

```bash
python scripts/prepare-logo.py [SOURCE.png] [DESTINATION.png]
```

Produit `LOGO_COMPLET_CENTRE_NEIGE.png` (fond transparent, tracé recoloré en neige `#EDEDED`)
à partir d'une variante du logo, pour le header sombre.
