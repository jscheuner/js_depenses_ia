# Plan directeur — `js_depenses_ia`

Module Odoo 18 Community de saisie automatisée des tickets de dépense
par scan ou photo, avec extraction assistée par intelligence artificielle
et apprentissage continu à partir des corrections manuelles.

---

## Objectif

Photographier un ticket de caisse depuis un téléphone, en extraire
automatiquement l'en-tête et les lignes de charge, affecter le bon compte
comptable, et produire l'écriture au grand livre — **sans jamais laisser
passer une erreur de centime**.

---

## Décisions d'architecture actées

| Sujet | Décision |
|---|---|
| Nom technique | `js_depenses_ia` (orthographe corrigée à la demande) |
| Modèle de saisie | Modèles entièrement neufs. **Aucun héritage de `account.move`** |
| Pièce comptable | Écriture au grand livre produite uniquement à la comptabilisation |
| Mode TVA | Hérité du compte : taxe incluse → ligne TTC, taxe exclue → ligne HT |
| Moteur de calcul | `account.tax.compute_all()` exclusivement, jamais de calcul maison |
| Contrepartie | Compte de passage sur journal dédié |
| Contrôle | Total calculé = total imprimé, sinon comptabilisation refusée |
| Apprentissage | Application automatique avec mention « appris de vos corrections » |
| Moteurs IA | Ollama local **et** API cloud, configurables simultanément |
| Analyse IA | Toujours asynchrone, via `queue_job` (OCA), un job par ticket — jamais dans la requête web |

---

## État d'avancement

### Livré

- [x] Modèles de saisie : ticket, lignes, récapitulatif TVA
- [x] Analyse des montants au format suisse (`Decimal`, apostrophe de milliers)
- [x] Normalisation des libellés pour l'apprentissage
- [x] Calcul de TVA délégué au moteur natif, modes inclus et exclu
- [x] Contrôles bloquants au centime, globaux et taux par taux
- [x] Génération de l'écriture au grand livre + vérifications a posteriori
- [x] Journal des corrections manuelles
- [x] Règles apprises avec renforcement et désactivation automatique
- [x] Correspondance enseigne imprimée → fournisseur
- [x] Couche IA multi-provider (Ollama, OpenAI, Anthropic, Mistral)
- [x] Prétraitement d'image et OCR Tesseract
- [x] Boucle de correction arithmétique avec relance ciblée
- [x] Affectation comptable par liste restreinte de candidats
- [x] Vues liste, formulaire, kanban mobile, recherche
- [x] Groupes de sécurité, droits d'accès, règles multi-société
- [x] Réception des tickets par courriel (alias + analyse différée)
- [x] Dépôt groupé de photos depuis Odoo
- [x] Lien retour écriture comptable → ticket
- [x] Analyse asynchrone via `queue_job`, un job par ticket (canal
      exclusif, lecture vision puis structuration texte séparées, option
      `json_from_vision`)
- [x] Vérification de disponibilité du moteur IA avant tout appel long
      (échec rapide et propre si hors ligne)
- [x] 65 tests automatisés

### Reste à faire

- [ ] **Installer et tester le module sur le serveur** (jamais exécuté à ce jour)
- [ ] Widget de capture photo dédié (voir réserve ci-dessous)
- [ ] Configurer un domaine d'alias sur le serveur (aucun n'existe)
- [ ] Tableau de bord de mesure de la progression de l'apprentissage
- [ ] Rapprochement automatique du compte de passage

---

## Réserve importante

Le module **n'a jamais été installé ni exécuté**. Il a été écrit hors
ligne, sans accès SSH au serveur. La syntaxe Python et XML est validée, le
comportement d'Odoo sur la TVA a été vérifié expérimentalement par
XML-RPC, mais **l'installation reste à éprouver**.

Le widget de capture photo en OWL a été délibérément écarté : un fichier
JavaScript défectueux rend inutilisable l'intégralité de l'interface
Odoo, et il était impossible de le tester. Le widget natif retenu ouvre
déjà l'appareil photo depuis un navigateur mobile.

---

## Documentation

| Fichier | Contenu |
|---|---|
| `00_PLAN.md` | Ce document |
| `01_CONTEXTE.md` | Environnement, serveur, plan comptable et taxes relevés |
| `02_ARCHITECTURE.md` | Modèles, relations, cycle de vie |
| `03_TVA_PRECISION.md` | Règles de calcul et garde-fous au centime |
| `04_APPRENTISSAGE.md` | Corrections, règles, normalisation, renforcement |
| `05_IA.md` | Moteurs, prompts, OCR, boucle de correction |
| `06_DEPLOIEMENT.md` | Installation, configuration, tests, dépannage |
