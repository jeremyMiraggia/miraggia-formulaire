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

Arborescence source attendue : `<genre>/<tranche d'âge>/<NN.PRENOM>/<images>`.
Le genre est déduit du nom du dossier (FEMME / HOMME), la tranche d'âge de ses chiffres
(borne haute ≤ 15 ans ⇒ genre `Enfant`), le prénom du nom du dossier sans son préfixe numérique.

### Classification automatique

Chaque image est classée `plein-pied`, `visage` ou `profil` (détection de visage OpenCV +
silhouette sur fond de studio). Le script signale en fin d'exécution les cas à vérifier
(photo ignorée, type manquant).

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
