# Brief — Page Catalogue Mannequins Miraggia

> Document de passation. Contient le contexte du projet existant + la spec de la page à construire.

---

## 1. Contexte projet

**Miraggia** est un studio qui produit des visuels mode avec des mannequins IA. Il existe déjà un mini-site déployé sur **Vercel** (via GitHub, déploiement auto à chaque commit) :

| Fichier | Rôle |
|---|---|
| `miraggia-brief.html` | Formulaire client en 5 étapes → envoie vers Supabase |
| `miraggia-dashboard.html` | Dashboard admin → lit les briefs depuis Supabase |
| `index.html` | Redirection racine vers le formulaire |
| `catalogue.pdf` | Catalogue mannequins actuel (PDF statique — **à remplacer par la nouvelle page**) |

URL de prod : `miraggia-formulaire.vercel.app`

Le formulaire et le dashboard sont des fichiers HTML **autonomes** : tout le CSS et le JS sont inline, aucune dépendance de build, aucun framework. **La page catalogue doit suivre exactement la même approche** — un seul fichier `.html` qu'on dépose dans le repo.

---

## 2. Charte graphique (à respecter strictement)

```css
--abysse:   #00445D;   /* bleu profond — couleur principale, boutons, accents */
--nuit:     #1A1A33;   /* header, fonds sombres */
--pistache: #BCFA91;   /* vert clair — accents, CTA secondaires */
--brume:    #ADC8C8;   /* gris-vert doux */
--lilas:    #D0A7F5;   /* violet — accents tertiaires */
--neige:    #EDEDED;   /* fond de page */
```

**Typo :** Poppins (Google Fonts), poids 300 à 700.
```html
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
```

**Logo :** `LOGO_COMPLET_CENTRE_NEIGE.png` — version claire, à afficher sur le header sombre (`--nuit`), hauteur ~50px, **sans encadré ni fond blanc**. Le PNG d'origine a un fond noir : il faut le rendre transparent (voir tâche 1).

**Style général à reproduire :** cartes blanches, bordures `1px solid #e8eaf0`, `border-radius: 12px`, ombres très légères, beaucoup d'air. Regarder `miraggia-brief.html` et `miraggia-dashboard.html` pour le ton exact avant de coder.

---

## 3. Source des photos

Dossier local : `D:\CHIMP_ME\MANNEQUINS`

Structure observée :
- 2 dossiers racine : **hommes** / **femmes**
- dans chacun : des dossiers par **catégorie d'âge** (les enfants sont inclus dans cette arborescence)
- dans chaque catégorie d'âge : un dossier **par prénom de mannequin**
- chaque dossier prénom contient **au moins 3 images utiles** : plein pied, visage, profil

⚠️ Les noms exacts des dossiers d'âge ne sont pas connus — **explorer l'arborescence réelle et s'y adapter**, ne rien coder en dur.

Les images sont **lourdes** et doivent être compressées.

---

## 4. Tâches

### Tâche 1 — Préparer le logo
Prendre `LOGO_COMPLET_CENTRE_NEIGE.png`, rendre le fond noir transparent (les pixels sombres RGB < 40 deviennent alpha 0), l'embarquer en base64 dans la page ou le déposer en tant que fichier dans le repo.

### Tâche 2 — Compresser et réorganiser les photos

Produire un dossier `output/mannequins/` prêt à commiter :

```
mannequins/
  femmes/
    <tranche-age>/
      <prenom>/
        01-plein-pied.jpg
        02-visage.jpg
        03-profil.jpg
        ...
  hommes/
    ...
```

Règles de compression :
- Redimensionner à **1200px de large max** (conserver le ratio)
- JPEG **qualité 80**, progressif
- Générer en plus une **miniature** `thumb.jpg` (400px de large) par mannequin, à partir de la photo plein pied → c'est elle qui s'affiche dans la grille, pour que la page charge vite
- Slugifier les noms de dossiers et fichiers (minuscules, sans accents ni espaces) — mais **conserver le prénom d'origine** dans le JSON pour l'affichage

Viser **< 200 Ko par image** et **< 60 Ko par miniature**.

### Tâche 3 — Générer `mannequins.json`

Un fichier à la racine du repo, structure suggérée :

```json
{
  "generated": "2026-09-15",
  "filters": {
    "genre": ["Femme", "Homme", "Enfant"],
    "age": ["...récupéré depuis les dossiers..."]
  },
  "mannequins": [
    {
      "id": "femmes-25-35-sofia",
      "prenom": "Sofia",
      "genre": "Femme",
      "age": "25-35 ans",
      "thumb": "mannequins/femmes/25-35-ans/sofia/thumb.jpg",
      "photos": [
        "mannequins/femmes/25-35-ans/sofia/01-plein-pied.jpg",
        "mannequins/femmes/25-35-ans/sofia/02-visage.jpg",
        "mannequins/femmes/25-35-ans/sofia/03-profil.jpg"
      ]
    }
  ]
}
```

Le script qui le génère doit être **relançable** : on ajoute un mannequin dans `D:\CHIMP_ME\MANNEQUINS`, on relance, le JSON est à jour. Garder ce script dans le repo (`scripts/build-catalogue.py` par ex.).

**Enfants :** s'ils sont dans une catégorie d'âge sous hommes/femmes, les remonter en genre `"Enfant"` pour que le filtre marche comme demandé. À arbitrer selon l'arborescence réelle.

### Tâche 4 — Construire `catalogue-mannequins.html`

**Fichier HTML autonome** (CSS + JS inline) qui charge `mannequins.json` en `fetch` et affiche tout dynamiquement.

#### Structure
- **Header sombre** (`--nuit`) avec le logo + mention "Catalogue mannequins" + bouton retour vers `miraggia-brief.html`
- **Hero** dégradé `--abysse` → `--nuit` avec un titre et une phrase d'intro
- **Barre de filtres** collante en haut au scroll
- **Grille de cartes** responsive
- **Modal** au clic sur une carte

#### Filtres (cumulables)
- **Genre** : Tous / Femme / Homme / Enfant
- **Âge** : généré dynamiquement depuis le JSON, pas codé en dur
- **Recherche texte** par prénom
- Afficher en permanence le **nombre de résultats** ("12 mannequins")
- Un bouton **Réinitialiser** quand au moins un filtre est actif
- Le filtrage se fait **côté client**, instantané, sans rechargement

#### Grille
- `grid-template-columns: repeat(auto-fill, minmax(200px, 1fr))`
- Carte = miniature en ratio 3/4 + prénom + petits tags (genre, âge)
- Hover : légère élévation + ombre
- **Lazy loading** des images (`loading="lazy"`)
- Animation d'apparition discrète au filtrage (fade/scale, pas plus de 150ms)

#### Modal au clic
- Grande photo + **vignettes cliquables** pour naviguer entre les photos du mannequin
- Navigation clavier : flèches gauche/droite entre photos, `Échap` pour fermer
- Fermeture au clic en dehors
- Infos : prénom, genre, tranche d'âge
- Bouton "Retour au formulaire" qui renvoie vers `miraggia-brief.html`
- Bloquer le scroll de la page quand le modal est ouvert

#### États à gérer
- **Chargement** : skeleton ou spinner pendant le `fetch` du JSON
- **Aucun résultat** : message clair + bouton pour réinitialiser les filtres
- **Erreur de chargement** du JSON : message lisible, pas une page blanche

#### Responsive
Mobile en priorité : 2 colonnes sur petit écran, filtres qui passent en scroll horizontal, modal plein écran.

### Tâche 5 — Mettre à jour le lien dans le formulaire

Dans `miraggia-brief.html`, étape "Mannequins & Poses", le lien "voir le catalogue" pointe actuellement vers `catalogue.pdf`. Le faire pointer vers `catalogue-mannequins.html`.

Chercher :
```html
<a href="catalogue.pdf" target="_blank" ...>
```

⚠️ Le formulaire est **bilingue FR/EN** via une fonction `lang(fr, en)`. Ne pas casser les template literals existants en modifiant cette ligne.

---

## 5. Contraintes techniques

- **Aucun framework, aucun build.** HTML + CSS + JS vanilla, un seul fichier.
- **Pas de `localStorage` ni `sessionStorage`.**
- Chemins **relatifs** dans le JSON (le site est servi à la racine du domaine Vercel).
- La page doit rester utilisable avec ~100 mannequins sans ramer.
- Tester en local (`python -m http.server` par ex.) avant de commiter — le `fetch` du JSON ne marche pas en `file://`.

---

## 6. Livrables attendus

1. `catalogue-mannequins.html` — la page
2. `mannequins.json` — les données
3. `mannequins/` — les images compressées
4. `scripts/build-catalogue.py` — le script relançable
5. `miraggia-brief.html` — mis à jour avec le nouveau lien

Le tout à commiter dans le repo GitHub connecté à Vercel → déploiement automatique.
