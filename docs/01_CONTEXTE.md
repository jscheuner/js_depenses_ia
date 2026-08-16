# Contexte technique relevé

Toutes les informations ci-dessous ont été constatées sur l'installation
réelle, par interrogation XML-RPC, et non supposées.

---

## Serveur

| Élément | Valeur |
|---|---|
| Hôte | `192.168.1.68:8069` |
| Version Odoo | **18.0** (`server_version_info` : `[18, 0, 0, 'final', 0, '']`) |
| Bases disponibles | `scheuner-test`, `scheuner_ocr_ia_dev` |
| Base de développement | `scheuner_ocr_ia_dev` |
| Accès SSH | Clé publique de `joel@joel-maison` **non installée** à ce jour |

Un ancien serveur `192.168.1.19` (Odoo 10) figure dans la configuration
SSH mais ne répond plus.

---

## Société

| Élément | Valeur |
|---|---|
| Raison sociale | J.Scheuner Sàrl |
| Devise | CHF (arrondi 0.01) |
| Pays fiscal | Suisse |
| Localisation installée | `l10n_ch`, `l10n_ch_qriban` |

Modules comptables installés : `account`, `base_vat`, `l10n_ch`,
`l10n_ch_qriban`, `mail`. La comptabilité analytique et les modules
entreprise ne sont pas présents.

---

## Taxes d'achat

La localisation suisse fournit chaque taux en **double** : une version
hors taxe et une version taxe comprise. C'est ce doublement qui rend
possible la règle retenue par le module.

| Taux | Version hors taxe | Version taxe comprise |
|---|---|---|
| 8.1 % (normal actuel) | id 148 | id 149 |
| 2.6 % (réduit actuel) | — | id 159, 161 |
| 7.7 % (ancien normal) | id 132, 134 | id 133, 135 |
| 3.7 % (hébergement) | id 127, 130 | id 128, 131 |
| 2.5 % (ancien réduit) | id 92, 93 | id 99, 100 |

Le champ déterminant est `price_include_override`, de valeur
`tax_included` ou `tax_excluded`. Le champ booléen `price_include` en
découle.

---

## Plan comptable

- **126 comptes** de type charge, charge directe ou immobilisation
- **50 d'entre eux portent déjà une taxe par défaut** (`account.account.tax_ids`)

Extrait :

```
1500 Machines et appareils        -> TVA 8.1% invest. (TN, INCL)
1510 Outillage                    -> TVA 8.1% invest. (TN, INCL)
4200 Achats de marchandises       -> TVA 8.1% achat B&S (TN, INCL)
4205 Achats tube aluminium        -> TVA 8.1% achat B&S (TS)
4210 Achats tubes acier           -> TVA 8.1% achat B&S (TS)
```

Les 76 comptes restants n'ont aucune taxe par défaut : sur ces comptes,
la taxe devra être choisie à la saisie ou déduite du taux lu sur le
ticket.

---

## Particularités Odoo 18 constatées

Ces points ont été vérifiés car ils diffèrent des versions antérieures et
conditionnent le code du module.

| Champ | Constat |
|---|---|
| `account.account.company_id` | **N'existe plus** |
| `account.account.company_ids` | Many2many — c'est le champ à utiliser |
| `account.tax.company_id` | Many2one, inchangé |
| `account.tax.price_include` | Booléen calculé depuis `price_include_override` |
| `account.journal.suspense_account_id` | Présent — sert de détecteur de déséquilibre |
| `account.tax.repartition.line.document_type` | Champ requis (`invoice` / `refund`) |

Les vues utilisent `<list>` et non `<tree>`, l'attribut `attrs` a disparu
au profit d'expressions directes (`invisible="..."`), et le chatter
s'insère par `<chatter/>`.

---

## Journaux existants

Aucun journal n'est encore dédié aux dépenses. Journaux pertinents :

```
OD     general   Opérations Diverses
EXJ    general   Journal de frais
JC     bank      Journal de caisse            -> 1000 Caisse
JB     bank      Journal de banque BCV        -> 1020 BCV
JR     cash      Journal de remboursement JS  -> 2160 Avance de Joël (caisse)
```

Un journal dédié et un compte de passage restent à créer, conformément à
l'organisation retenue.

---

## Outillage disponible

**Poste de développement** : Ollama opérationnel (`gemma4:26b`, `llava`,
plus des modèles cloud `glm-5.2`, `deepseek-v4-pro`), `tesseract` et
`pdftoppm` installés.

**Serveur Odoo** : présence de `requests`, `Pillow`, `pytesseract` et
`tesseract` **non vérifiée** faute d'accès SSH. Le module se dégrade
proprement en leur absence — voir `06_DEPLOIEMENT.md`.

---

## Antériorité réutilisée

Le script `~/odoo_ai/main.py` préexistant extrayait déjà des factures
fournisseurs via Ollama et XML-RPC. Deux idées en ont été reprises :

- la recherche des comptes déjà employés avec un fournisseur donné, comme
  signal de rattachement le plus fiable ;
- la restriction de la liste de comptes soumise au modèle, plutôt que
  l'envoi du plan comptable entier.

Sa faiblesse — l'absence de tout contrôle arithmétique sur les montants
extraits — est précisément ce que le présent module corrige.
