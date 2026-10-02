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

- Source par défaut : `H:\Drive partagés\MIRAGGIA SAS\CATALOGUE\CATALOGUE 2026\POUR JEREM`
  (option `--src` pour un autre dossier).
- Sortie : `mannequins/` (images compressées à 1200 px + une miniature 3/4 de 400 px par photo :
  `thumb.jpg`, `thumb-02.jpg`…) et `mannequins.json` à la racine du repo.
- Relançable : seuls les mannequins dont les fichiers source ont changé sont ré-encodés
  (cache dans `scripts/.catalogue-cache.json`). `--force` ré-encode tout.
- `--dry-run` affiche la classification et le JSON sans rien écrire.
- Les dossiers de sortie des mannequins retirés de la source sont supprimés automatiquement.

### Arborescence source

Un mannequin est repéré au fait que **son dossier contient des images**. Son genre et sa
catégorie sont lus sur la chaîne de ses dossiers parents, le plus profond l'emportant — la
profondeur n'est donc pas imposée. Toutes ces formes fonctionnent :

```
TENUES FEMMES 20_30 ANS/1.AMBRE/      genre + âge sur le même dossier
TENUES _+SIZE/FEMMES/3.LOLA/          catégorie puis genre
TENUES ENFANTS/BÉBÉS/1.MILAN/         chapeau puis catégorie enfant
FEMMES/20-30 ANS/01.MILA/             genre puis âge (ancienne arborescence)
```

- **Genre** : nom contenant FEMME / WOMEN / FILLE, ou HOMME / MEN / GARCON.
- **Catégorie** : ses chiffres (`20_30 ANS` → « 20-30 ans », borne haute ≤ 15 ans ⇒ genre
  `Enfant`), ou son nom s'il n'en a pas — `+SIZE` → « Plus size », `BÉBÉS` → « Bébé »,
  `TENUES FILLES` → « Fille », `TENUES GARCONS` → « Garçon » (ces trois dernières étant
  des catégories du genre `Enfant`).
- **Prénom** : le nom du dossier sans son préfixe numérique.
- Un préfixe `TENUES ` est ignoré partout.

Les enfants sortent sous `mannequins/enfants/<catégorie>/<prénom>/`, les autres sous
`mannequins/<femmes|hommes>/<catégorie>/<prénom>/`.

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

Clé = `id` du mannequin dans `mannequins.json`, valeurs = `plein-pied`, `visage`, `mi-corps`, `profil` ou `skip`.
Relancer le script : les mannequins concernés sont ré-encodés.

### Imposer le type selon le numéro de photo (par catégorie)

Quand une catégorie suit une convention fixe (ex. femmes 20-30 ans : `PRENOM-1.jpg` = visage,
`-2` = mi-corps, `-3` = plein pied), on l'indique une fois pour toutes dans le même fichier,
sous la clé `_par_numero`, par préfixe d'`id` (ou `*` pour tous) :

```json
{
  "_par_numero": {
    "femmes-20-30-ans": { "1": "visage", "2": "mi-corps", "3": "plein-pied" }
  }
}
```

La détection automatique n'est alors plus utilisée pour ces photos.

## Logo

```bash
python scripts/prepare-logo.py [SOURCE.png] [DESTINATION.png]
```

Produit `LOGO_COMPLET_CENTRE_NEIGE.png` (fond transparent, tracé recoloré en neige `#EDEDED`)
à partir d'une variante du logo, pour le header sombre.
