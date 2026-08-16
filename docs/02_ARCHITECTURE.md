# Architecture

## Principe fondateur

Le module ne dérive d'aucun modèle comptable existant. `account.move`
n'apparaît qu'en **sortie**, à la comptabilisation, et n'est référencé
que par un champ de liaison.

```
Photo / Scan
     |
     v
js.depense.ticket ------------< js.depense.ticket.line
     |                                    |
     |                                    v
     |                          js.depense.ticket.tax  (récapitulatif TVA)
     |
     |  [Comptabiliser]
     v
account.move  (écriture au grand livre)
```

Les modèles consultés en lecture seule sont `account.account` (plan
comptable), `account.tax` (moteur de calcul), `account.journal` et
`res.partner`.

---

## Modèles

### `js.depense.ticket`

Le document central. Hérite de `mail.thread` et `mail.activity.mixin`
pour le suivi et les discussions.

**Identification** : `name` (séquence `TICK/2026/0001`), `state`,
`company_id`, `currency_id`, `user_id`.

**En-tête** : `partner_id`, `partner_name_raw` (enseigne telle
qu'imprimée), `ticket_date`, `accounting_date`, `reference`,
`description`, `journal_id`, `counterpart_account_id`, `payment_kind`.

**Montants** :

| Champ | Rôle |
|---|---|
| `amount_untaxed`, `amount_tax`, `amount_total` | Calculés depuis les lignes |
| `amount_total_ticket` | **Total imprimé sur le ticket** — la référence opposable |
| `amount_difference` | Écart entre les deux |
| `is_reconciled` | Vrai si l'écart est nul |
| `difference_message` | Explication affichée à l'utilisateur |

**Sortie** : `move_id`, `move_state`.

**Traçabilité** : `ai_provider_id`, `ai_vision_text` (lecture libre
produite par le modèle de vision, avant structuration), `ai_raw_json`,
`ai_confidence`, `ai_log_ids`, `correction_ids`.

### `js.depense.ticket.line`

Une ligne de charge.

`name`, `name_normalized` (clé d'apprentissage, calculée et indexée),
`account_id`, `tax_ids`, `quantity`, `price_unit`, et les montants
calculés `price_subtotal` / `price_tax` / `price_total`.

Champs de traçabilité : `account_source` (`manual`, `ai`, `learned`,
`account`), `account_rule_id`, `ai_confidence`, `ai_account_hint`.

Le champ `tax_is_included` indique si `price_unit` s'entend TTC ou HT ;
`price_mode_label` l'affiche en clair dans la liste.

### `js.depense.ticket.tax`

Récapitulatif par taux, reconstruit à chaque modification.

`tax_id`, `tax_rate`, `price_include`, `base_amount`, `tax_amount`
(calculés), `tax_amount_ticket` (TVA imprimée, saisie), `difference`,
`has_difference`.

Cette table permet le contrôle le plus fin : sur un ticket mêlant 8.1 %
et 2.6 %, chaque taux est confronté séparément à ce qu'imprime le ticket.

### Modèles d'apprentissage

Décrits en détail dans `04_APPRENTISSAGE.md` :
`js.depense.correction`, `js.depense.account.rule`,
`js.depense.partner.alias`.

### Modèles d'analyse

`js.ai.provider` (moteur configurable), `js.ai.log` (trace des échanges),
et l'`AbstractModel` `js.ticket.extractor` qui orchestre la chaîne.
L'analyse s'exécute en tâche de fond via `queue_job` (OCA, dépendance du
module) : `js.depense.ticket._job_batch_vision()` et `_job_batch_text()`
sont les deux méthodes enfilées par lot. Détail complet dans `05_IA.md`.

---

## Cycle de vie

```
draft ──▶ analyzing ──▶ to_validate ──▶ validated ──▶ posted
  │                          │              │
  └──────────────────────────┴──────────────┴──────▶ cancel
```

| État | Signification |
|---|---|
| `draft` | Créé, pièces jointes en cours d'ajout |
| `analyzing` | Analyse IA enfilée ou en cours de traitement par `queue_job` |
| `to_validate` | Données extraites, vérification humaine requise |
| `validated` | Contrôles passés, apprentissage consolidé |
| `posted` | Écriture générée, ticket verrouillé |
| `cancel` | Abandonné |

La validation déclenche `_check_before_validation()` puis
`_learn_from_ticket()`. La comptabilisation ajoute `_check_posting_setup()`,
`_create_account_move()`, `_verify_move()` puis `_post_move()`, qui valide
l'écriture elle-même (`account.move.action_post()`). Un ticket à l'état
« Comptabilisé » correspond ainsi toujours à une écriture elle-même
validée, jamais laissée en brouillon.

Un ticket comptabilisé ne peut être ni modifié, ni supprimé, ni remis en
brouillon tant que son écriture existe.

---

## Structure des fichiers

```
js_depenses_ia/
├── __manifest__.py
├── models/
│   ├── js_depense_ticket.py          Document central, comptabilisation
│   ├── js_depense_ticket_line.py     Lignes, calcul TVA, journal des corrections
│   ├── js_depense_ticket_tax.py      Récapitulatif par taux
│   ├── js_depense_correction.py      Journal des corrections
│   ├── js_depense_account_rule.py    Règles apprises
│   ├── js_depense_partner_alias.py   Correspondance enseigne/fournisseur
│   ├── js_ai_provider.py             Moteurs IA
│   ├── js_ai_log.py                  Trace des échanges
│   ├── res_company.py                Paramètres société
│   ├── res_config_settings.py        Écran de configuration
│   └── account_journal.py            Contrepartie par journal
├── utils/
│   ├── amount_parser.py              Analyse Decimal des montants
│   └── text_normalizer.py            Normalisation des libellés
├── services/
│   ├── ocr.py                        Prétraitement image, PDF, Tesseract
│   └── ticket_extractor.py           Chaîne d'extraction complète
├── views/            9 fichiers XML
├── security/         groupes, droits, règles multi-société
├── data/             séquence, moteurs IA préconfigurés,
│                     canaux et fonctions queue_job
├── static/src/scss/  ajustements d'affichage et confort mobile
└── tests/            5 fichiers, 40 tests
```

Dépend du module OCA `queue_job` (traitement asynchrone, voir `05_IA.md`).

---

## Sécurité

Deux groupes :

- **Saisie des tickets** (`group_js_depense_user`) : créer, modifier,
  vérifier. Ne peut pas comptabiliser.
- **Comptabilisation et configuration** (`group_js_depense_manager`) :
  implique le précédent, ajoute la comptabilisation, la gestion des
  règles et la configuration des moteurs.

Toutes les tables portent une règle globale de cloisonnement par société.
Le bouton de comptabilisation est réservé au second groupe.
