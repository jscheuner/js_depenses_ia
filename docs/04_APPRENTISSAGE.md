# Apprentissage par corrections

Objectif : que le système se trompe de moins en moins, et que cette
progression soit **mesurable** plutôt que supposée.

---

## Vue d'ensemble

Le journal des corrections n'est alimenté qu'à un seul moment : le clic
sur **« Valider »**. Ni la création du ticket, ni l'analyse IA, ni une
sauvegarde intermédiaire n'y écrivent quoi que ce soit — ces étapes ne
font que fixer, ou déplacer, une **valeur de référence** à laquelle la
valeur finalement retenue sera comparée.

```
  Création du ticket           Analyse IA / règle apprise
  (saisie manuelle)            appliquée automatiquement
       |                                |
       v                                v
  Référence fixée              Référence (ré)actualisée
  = valeurs de départ          = valeurs proposées par le système
       |________________________________|
                      |
                      v
      L'utilisateur modifie (ou non) des champs,
      enregistre une ou plusieurs fois : aucune
      trace n'est encore écrite
                      |
                      v
              Clic sur « Valider »
                      |
     Comparaison : référence  vs  valeur retenue
                      |
        ┌─────────────┴──────────────┐
        │ écart                      │ aucun écart
        v                            v
  js.depense.correction        confirm_count += 1
        │                            │
        └─────────────┬──────────────┘
                       v
         js.depense.account.rule  (création ou renforcement)
```

Deux signaux sont exploités, et non un seul : la correction explicite,
mais aussi **la validation sans retouche**, qui confirme la pertinence
d'une proposition. Ignorer le second reviendrait à n'apprendre que des
échecs.

Le déplacement de la référence (« Référence (ré)actualisée » ci-dessus)
n'est pas une correction : c'est le système qui propose une nouvelle
valeur, pas l'utilisateur qui en corrige une. Seul un écart persistant
**entre cette référence et ce que l'utilisateur valide au final**
constitue une correction (voir « La référence » ci-dessous).

---

## La normalisation, condition de tout le reste

Un ticket ne réimprime jamais deux fois exactement le même libellé.
Sans normalisation, aucune règle ne se redéclencherait et le dispositif
resterait lettre morte.

```
"COCA COLA 50CL 2x1.90"    ──┐
                             ├──▶  "coca cola"
"Coca-Cola 50 cl  3x1.90"  ──┘
```

Traitements appliqués par `normalize_label()` :

1. suppression des accents (`Café` → `cafe`) ;
2. réduction des unités chiffrées à leur unité seule (`50cl` → `cl`) ;
3. **suppression des nombres** — quantités, prix, lots, codes-barres ;
4. suppression de la ponctuation, compression des espaces ;
5. retrait des mots vides (`de`, `pour`, `ttc`, `pce`, `kg`…).

Pour les enseignes, `normalize_partner()` retire en outre les formes
juridiques (`SA`, `Sàrl`, `GmbH`, `AG`) et les numéros de succursale :

```
"MIGROS M-Budget Lausanne 042"  ->  "migros m budget lausanne"
"COOP Pronto SA"                ->  "coop pronto"
```

Un libellé de moins de 4 caractères, ou dépourvu de lettre, est jugé trop
générique et ne fonde aucune règle (`is_key_usable()`).

---

## `js.depense.correction` — le journal brut

Table en ajout seul, jamais purgée. Chaque divergence entre une valeur
proposée et la valeur retenue y est consignée.

| Champ | Rôle |
|---|---|
| `ticket_id`, `line_id` | Origine |
| `field_name`, `field_label` | Champ corrigé |
| `source` | `ai`, `learned`, `account`, `manual` — d'où venait la valeur |
| `value_old`, `value_new` | Avant / après, sous forme lisible |
| `partner_id`, `label_raw`, `label_normalized` | Contexte figé |
| `ai_provider_id`, `ai_model`, `ai_confidence` | Qui avait proposé, avec quelle assurance |
| `user_id`, `create_date` | Qui a corrigé, quand |

Le champ `source` est essentiel : il distingue une erreur du modèle d'une
simple saisie manuelle, et permet de mesurer séparément la qualité de
l'IA et celle des règles.

L'enregistrement d'une correction **n'échoue jamais** : toute exception
est capturée et journalisée. L'apprentissage ne doit en aucun cas
empêcher un utilisateur d'enregistrer son ticket.

Champs suivis : sur l'en-tête `partner_id`, `ticket_date`, `reference`,
`description`, `amount_total_ticket`, `journal_id` ; sur les lignes
`account_id`, `tax_ids`, `price_unit`, `name`.

---

## `js.depense.account.rule` — les règles

| Champ | Rôle |
|---|---|
| `label_key` | Clé normalisée déclenchant la règle |
| `label_sample` | Libellé d'origine, pour lisibilité humaine |
| `partner_id` | Vide = règle générale |
| `account_id`, `tax_ids` | Affectation retenue |
| `hit_count` | Nombre de propositions |
| `confirm_count` | Validations sans retouche |
| `reject_count` | Corrections après application |
| `confidence` | Taux de succès estimé |
| `priority`, `active` | Arbitrage et mise hors jeu |

### Confiance

```python
confidence = (confirm_count + 1) / (confirm_count + reject_count + 2)
```

L'ajout d'une observation positive et d'une négative fictives évite
qu'une règle vue une seule fois affiche d'emblée 100 % de confiance. Une
règle neuve démarre à 0.50 et gagne en assurance avec l'usage.

### Recherche, du plus spécifique au plus général

1. correspondance exacte pour ce fournisseur ;
2. correspondance partielle pour ce fournisseur ;
3. correspondance exacte, toutes enseignes confondues ;
4. correspondance partielle, toutes enseignes confondues ;
5. rapprochement approximatif (indice de Jaccard ≥ 0.60).

La correspondance partielle teste toutes les sous-séquences de mots :
`essence sp shell` retrouve une règle enregistrée sur `essence` ou
`essence sp`.

### Renforcement et arbitrage des contradictions

- Compte identique → `confirm_count += 1`.
- Compte différent → `reject_count += 1`. Si les rejets dépassent les
  confirmations, la règle **bascule sur le nouveau compte** : la dernière
  intention de l'utilisateur fait foi.
- Une règle est **désactivée automatiquement** dès que
  `reject_count ≥ 3` et `reject_count > 2 × confirm_count`.

Ce dernier mécanisme est la protection contre la pollution : une règle
née d'une correction malheureuse se retire d'elle-même du jeu.

---

## `js.depense.partner.alias` — les enseignes

Sans cette table, le fournisseur serait à re-saisir sur chaque ticket :
les enseignes impriment succursales et abréviations de façon très
variable.

Recherche en trois passes : alias appris exact, similarité sur les alias
connus (seuil 0.70), puis recherche dans le carnet d'adresses sur la clé
normalisée (seuil 0.75).

Si l'utilisateur rattache une enseigne à un autre fournisseur, la
correspondance est remplacée et son compteur remis à zéro.

---

## Mesure de la progression

`js.depense.correction.accuracy_report(company, days)` agrège les
corrections par champ et par origine sur une période glissante.

L'écran **Corrections**, groupé par champ, répond directement à la
question : *sur quoi le système se trompe-t-il encore ?* Les filtres
« Erreurs de l'IA » et « Règles contredites » séparent les deux causes.

Sans cette mesure, l'affirmation « le système apprend » resterait
invérifiable.

---

## Amorçage

Les étapes de saisie manuelle précèdent volontairement l'usage de l'IA :
chaque ticket saisi à la main crée des règles. Lorsque l'analyse
automatique est activée, elle démarre donc sur une base de connaissances
déjà constituée, et non à froid.

---

## Tests correspondants

`tests/test_learning.py` :

- une correction laisse une trace ;
- la validation transforme la correction en règle ;
- **un libellé corrigé une fois est affecté automatiquement ensuite**,
  y compris sous une formulation différente (`Café en grains 500g` puis
  `CAFE EN GRAINS 1KG`) ;
- la confiance progresse avec les confirmations ;
- une règle contredite bascule de compte ;
- une règle trop souvent fausse se désactive ;
- l'enseigne est apprise puis reconnue sous une variante ;
- un libellé trop court ne crée aucune règle.
