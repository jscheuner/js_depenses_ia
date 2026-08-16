# Précision au centime et traitement de la TVA

C'est l'exigence centrale du projet : la saisie doit correspondre **au
centime près** à ce qu'imprime le ticket. Ce document décrit comment
cette garantie est obtenue et, tout aussi important, ce qu'elle ne
couvre pas.

---

## Règle de saisie

La taxe est héritée du compte comptable, exactement comme sur une
facture fournisseur.

| Taxe portée par le compte | Sens de `price_unit` |
|---|---|
| `price_include = True` (taxe comprise) | Le montant saisi est **TTC** |
| `price_include = False` (hors taxe) | Le montant saisi est **HT** |
| Aucune taxe | Le montant est intégralement hors taxe |

La colonne « Mode » de la liste des lignes affiche `TTC`, `HT` ou
`sans TVA`, afin qu'aucune ambiguïté ne subsiste pendant la saisie.

En pratique suisse, les tickets de caisse impriment des montants TTC :
les comptes concernés doivent donc porter les taxes en version `INCL`.

---

## Les quatre garde-fous

### 1. Aucun `float` avant l'arrondi final

Tout montant lu sur un ticket transite par `Decimal`. Un `float` ne peut
pas représenter exactement `0.1`, et l'accumulation de ces écarts fausse
les totaux.

```python
Decimal(0.1)        # 0.1000000000000000055511151231257827
Decimal(str(0.1))   # 0.1   <- ce que fait le module
```

Formats reconnus, tous testés :

```
1'234.50    1’234.50    1 234,50    1.234,50    1,234.50
CHF 12.90   12.90 CHF   -12.90      (12.90)     12.90-
```

L'apostrophe suisse, droite comme typographique, est traitée comme
séparateur de milliers.

### 2. Aucun calcul de TVA réimplémenté

Le module **n'a pas sa propre formule de TVA**. Il appelle
`account.tax.compute_all()`, le moteur natif d'Odoo :

```python
taxes_res = line.tax_ids.compute_all(
    price_unit, currency=currency, quantity=quantity,
    product=None, partner=ticket.partner_id)
line.price_subtotal = taxes_res['total_excluded']
line.price_total = taxes_res['total_included']
```

C'est la seule façon d'obtenir strictement le même centime qu'une
facture fournisseur : arrondis, ordre des opérations et gestion des
taxes incluses sont identiques par construction.

Exemple à 8.1 % taxe comprise : `100.00 / 1.081 = 92.5069…` arrondi à
`92.51`, TVA `7.49`.

### 3. Contrôle bloquant avant validation

Le champ `amount_total_ticket` porte le total imprimé. Il est **renseigné
automatiquement par l'analyse**, et n'a pas à être saisi à la main.

Trois vérifications interdisent la validation :

1. toute ligne dépourvue de compte comptable ;
2. `|amount_total − amount_total_ticket|` supérieur à la tolérance ;
3. pour chaque taux dont la TVA imprimée est renseignée, un écart
   supérieur à la tolérance.

La tolérance vaut **0.00 par défaut**, ce qui impose une correspondance
exacte. C'est le réglage recommandé.

#### Cas où aucun total n'a pu être lu

Le contrôle n'étant possible qu'avec une référence, l'absence de total
le rend inopérant. Ce cas **n'interdit pas la validation** — exiger une
saisie manuelle sur chaque ticket serait trop lourd — mais il est signalé
sans ambiguïté :

- bandeau orange permanent sur le formulaire ;
- mention explicite dans le fil de discussion à la validation ;
- champ `is_reconciled` à faux, exploitable en filtre de liste.

C'est un affaiblissement assumé de la garantie : sur ces tickets, seule
la relecture humaine protège d'une ligne mal extraite. Le filtre
« Écart de montant » et l'absence de coche « Montants concordants »
permettent de les repérer et de les traiter en priorité.

### 4. Vérification a posteriori de l'écriture

Après création de l'écriture, et avant de considérer le ticket comme
comptabilisé :

- **aucune ligne ne doit toucher le compte d'attente du journal** —
  Odoo y déverse automatiquement tout déséquilibre, ce qui en fait un
  détecteur d'erreur remarquablement fiable ;
- débit total = crédit total ;
- débit total = total du ticket.

Toute anomalie lève une exception, ce qui **annule la transaction** :
ni écriture ni ticket comptabilisé ne subsistent.

---

## Comportement d'Odoo sur les écritures : résultat expérimental

Un point déterminant a été vérifié directement sur le serveur, en créant
puis supprimant des écritures de test.

**Constat** : sur une écriture de type `entry`, Odoo traite la balance
d'une ligne comme une **base hors taxe dans tous les cas**, que la taxe
soit « incluse dans le prix » ou non.

```
Base 100.00 + taxe 8.1% INCL  ->  TVA 8.10  ->  total 108.10
Base 100.00 + taxe 8.1% EXCL  ->  TVA 8.10  ->  total 108.10
```

Les deux modes donnent le même résultat : contrairement aux factures,
Odoo n'extrait pas la TVA du montant sur une écriture.

**Conséquence sur le code** : la gestion du TTC est assurée **en amont**,
au niveau du ticket. L'écriture reçoit toujours le montant **hors taxe** :

```python
'debit': line.price_subtotal   # et non price_unit
'tax_ids': [(6, 0, line.tax_ids.ids)]
```

Odoo régénère alors les lignes de TVA avec leurs `tax_tag_ids` et
répartitions correctes, ce qui garantit une **déclaration TVA suisse
juste**.

---

## Structure de l'écriture produite

Pour un ticket de 100.00 CHF TTC à 8.1 % :

| Compte | Débit | Crédit |
|---|---:|---:|
| 4200 Achats de marchandises | 92.51 | |
| 1170 TVA à récupérer (généré par Odoo) | 7.49 | |
| Compte de passage | | 100.00 |

Le compte de passage est ensuite soldé lors du rapprochement avec la
banque, la caisse ou la note de frais.

---

## Tickets à plusieurs taux

Cas courant en grande surface : 8.1 % sur l'entretien, 2.6 % sur
l'alimentaire. Chaque ligne porte sa propre taxe, déduite de son compte.
Le récapitulatif isole les taux et permet de confronter chacun d'eux à
ce qu'imprime le ticket.

Lorsque l'IA lit un taux différent de celui du compte, c'est **le taux lu
qui prime** : le module recherche une taxe de ce taux en conservant le
mode d'inclusion du compte, afin de ne jamais basculer involontairement
de TTC à HT (`_resolve_taxes()`).

---

## Ce que cette garantie ne couvre pas

Par honnêteté technique, trois limites doivent être connues.

**L'extraction reste faillible.** Aucun modèle, local ou distant, ne lit
de façon sûre un ticket thermique décoloré ou froissé. Les garde-fous ne
rendent pas l'IA exacte : ils garantissent qu'**une erreur ne passe pas
en silence**. Un écart bloque et s'affiche.

**Le total imprimé est saisi ou extrait.** Si l'IA lit mal le total *et*
mal une ligne de façon compensatoire, le contrôle peut être satisfait à
tort. C'est improbable mais pas impossible : d'où la validation humaine
obligatoire, et l'affichage de la pièce jointe à côté des montants.

**La tolérance est un compromis.** La porter au-delà de 0.00 autorise
mécaniquement de petits écarts. Elle ne devrait servir qu'aux arrondis
avérés, jamais à contourner un contrôle gênant.

---

## Tests correspondants

`tests/test_tax_computation.py` et `tests/test_account_move.py` :

- 100.00 TTC à 8.1 % → 92.51 HT + 7.49 TVA
- 100.00 HT à 8.1 % → 108.10 TTC
- ticket mixte 8.1 % + 2.6 %
- ligne sans taxe, ligne négative (remise)
- taxe héritée du compte
- blocage sur écart d'un centime
- blocage sans compte, sans total imprimé
- blocage sur TVA imprimée divergente
- tolérance explicite autorisant un écart
- écriture équilibrée, structure des lignes, absence de compte d'attente
- interdiction de double comptabilisation et de suppression
