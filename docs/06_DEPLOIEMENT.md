# Déploiement, configuration et dépannage

> **Avertissement** : le module n'a jamais été installé ni exécuté. Il a
> été développé hors ligne, l'accès SSH au serveur n'ayant pas pu être
> établi. La syntaxe Python et XML est validée et le comportement
> d'Odoo sur la TVA a été vérifié expérimentalement par XML-RPC, mais la
> première installation reste à éprouver.

---

## 1. Accès SSH

La clé publique de ce poste n'est pas installée sur `192.168.1.68` :

```
ssh-copy-id -i ~/.ssh/id_ed25519.pub odoo@192.168.1.68
```

Clé concernée : `joel@joel-maison`, empreinte
`SHA256:UxcHQ0pcEsjZMw0bORXYtNMgLMeaZ+OwvAzU20tyS8M`.

Vérification :

```
ssh odoo@192.168.1.68 "echo OK"
```

---

## 2. Prérequis serveur

À vérifier dans l'environnement Python d'Odoo :

```bash
python3 -c "import requests; print('requests OK')"
python3 -c "import PIL; print('Pillow OK')"
python3 -c "import pytesseract; print('pytesseract OK')"
which tesseract pdftoppm
```

| Composant | Rôle | Si absent |
|---|---|---|
| `requests` | Appels aux moteurs IA | **Analyse IA indisponible** ; saisie manuelle intacte |
| `Pillow` | Prétraitement d'image | Images transmises telles quelles |
| `pytesseract` | OCR via Python | Repli sur l'exécutable `tesseract` |
| `tesseract` | OCR | Mode vision seule, montants moins fiables |
| `pdftoppm` | Conversion PDF | Les PDF ne peuvent pas être analysés |

Le module se dégrade proprement : aucune de ces absences n'empêche
l'installation ni la saisie manuelle.

Installation si nécessaire (paquets Debian) :

```bash
sudo apt install tesseract-ocr tesseract-ocr-fra tesseract-ocr-deu poppler-utils
sudo pip3 install requests pillow pytesseract    # ou dans le venv d'Odoo
```

### `queue_job` (OCA) et mode multi-worker

L'analyse IA s'exécute **toujours** en tâche de fond, via le module OCA
`queue_job` (dépôt [`OCA/queue`](https://github.com/OCA/queue)) : c'est
ce qui évite qu'un modèle local lent ne fasse tuer le worker Odoo qui a
servi la requête (`limit_time_real`, ~120 s par défaut). Détail de la
conception (canaux, un job par ticket) dans `05_IA.md`.

Prérequis impératifs, sans quoi les jobs restent en attente indéfiniment :

1. **Installer `queue_job`** dans les addons du serveur, à côté de
   `js_depenses_ia` (il est déclaré en dépendance dans le manifeste et
   s'installe automatiquement avec le module, mais son code doit être
   présent dans `addons_path`) :
   ```bash
   git clone --branch 18.0 --depth 1 https://github.com/OCA/queue.git /opt/odoo18/oca-queue
   ```
   puis ajouter ce chemin à `addons_path` dans la configuration Odoo.
2. **`workers > 0`** dans la configuration Odoo (mode prefork). En mode
   développement (`workers = 0`, un seul thread), un job long bloque tout
   le serveur pour tous les utilisateurs — c'est le mode à éviter en
   production pour ce module en particulier.
3. **Limiter la capacité du canal** dans le fichier de configuration
   Odoo (`/etc/odoo18.conf` ou équivalent) — `queue.job.channel` ne
   porte aucun champ de capacité, cela se règle uniquement côté serveur :
   ```ini
   [queue_job]
   channels = root.js_depenses:1
   ```
   Sans cette ligne, `queue_job` traite les jobs du canal avec sa
   capacité par défaut (non limitée à 1), ce qui recharge le modèle sur
   le GPU à chaque bascule vision/texte.
4. **Lancer le « job runner »** de `queue_job` : il démarre automatiquement
   avec les workers Odoo (thread dédié à l'écoute des jobs en attente),
   rien à configurer côté systemd au-delà du redémarrage du service.
5. Vérifier après installation, dans **Réglages techniques → Tâches en
   file d'attente → Canaux**, que `root.js_depenses` et ses deux
   sous-canaux `vision`/`text` existent (créés par le module).

---

## 3. Transfert du module

```bash
rsync -av --delete \
    --exclude='__pycache__' --exclude='*.pyc' \
    /home/joel/dev_odoo18_ia/js_depenses_ia/ \
    odoo@192.168.1.68:/chemin/vers/addons/js_depenses_ia/
```

Le chemin exact est à relever dans le fichier de configuration
(`addons_path`), typiquement `/opt/odoo/addons` ou
`/opt/odoo18/custom-addons`.

Redémarrage :

```bash
ssh odoo@192.168.1.68 "sudo systemctl restart odoo18"
```

---

## 4. Installation

1. Activer le mode développeur.
2. **Applications → Mettre à jour la liste des applications**.
3. Rechercher « Dépenses IA » et installer.

En cas d'erreur, consulter le journal :

```bash
ssh odoo@192.168.1.68 "sudo journalctl -u odoo18 -n 100 --no-pager"
```

---

## 5. Configuration comptable

### Créer le journal dédié

Comptabilité → Configuration → Journaux → Créer

| Champ | Valeur suggérée |
|---|---|
| Nom | Journal des dépenses |
| Type | Divers |
| Code court | `DEP` |

### Créer le compte de passage

Comptabilité → Configuration → Plan comptable → Créer

| Champ | Valeur suggérée |
|---|---|
| Code | `2099` (à adapter) |
| Nom | Dépenses à rapprocher |
| Type | Passif courant |

Ce compte est crédité du total TTC de chaque ticket, puis soldé lors du
rapprochement avec la banque, la caisse ou la note de frais employé.

### Renseigner les paramètres

Comptabilité → Configuration → Paramètres → **Dépenses IA**

- Journal des dépenses
- Compte de contrepartie
- **Tolérance d'écart : 0.00** (correspondance exacte, recommandé)
- Moteur IA par défaut, relances, assistance OCR
- Application des règles apprises

### Vérifier les taxes des comptes

Point déterminant pour la précision : les comptes de charge utilisés sur
les tickets doivent porter les taxes en version **INCL** (taxe comprise),
puisque les tickets suisses impriment des montants TTC.

Sur les 126 comptes de charge de la base, 50 portent déjà une taxe par
défaut. Les 76 autres demanderont un choix manuel à la saisie.

---

## 6. Droits utilisateurs

Deux groupes, dans **Dépenses IA** :

- **Saisie des tickets** : créer, modifier, vérifier
- **Comptabilisation et configuration** : ajoute la comptabilisation, la
  gestion des règles et la configuration des moteurs

---

## 7. Moteur IA

Dépenses IA → Configuration → Moteurs IA

Le moteur **Ollama local** est actif par défaut sur
`http://localhost:11434`. Si Ollama tourne sur un autre poste — ce qui
est le cas ici, Ollama étant sur le poste de développement et non sur le
serveur — corriger l'URL, par exemple `http://192.168.1.XX:11434`, et
s'assurer qu'Ollama écoute sur le réseau :

```bash
OLLAMA_HOST=0.0.0.0 ollama serve
```

Utiliser le bouton **Tester la connexion**.

Pour un moteur distant (Anthropic, OpenAI, Mistral), il suffit de
sélectionner le **Type** : l'adresse du service est déterminée
automatiquement et n'est pas modifiable, ces éditeurs n'exposant qu'un
seul point d'entrée.

La clé se colle directement dans le champ **Clé d'API** de la fiche. Elle
est aussitôt rangée dans les paramètres système et n'est jamais
conservée sur l'enregistrement : le champ redevient vide à la
réouverture, un bandeau vert confirmant qu'une clé est bien enregistrée.
Aucune manipulation dans Paramètres techniques n'est nécessaire.

---

## 7 bis. Réception des tickets par courriel

Permet de photographier un ticket avec son téléphone et de l'envoyer par
courriel : le ticket est créé, analysé en arrière-plan, puis présenté à
la vérification.

### Prérequis

Un **domaine d'alias** doit exister. Sur la base de développement, aucun
n'est configuré à ce jour :

Paramètres → Technique → Courriel → **Domaines d'alias** → Créer
(par exemple `jscheunersarl.ch`).

La réception de courriels entrants doit par ailleurs être opérationnelle
(Paramètres → Technique → Courriel → Serveurs de messagerie entrants).

### Configuration

Comptabilité → Configuration → Paramètres → **Dépenses IA** →
*Réception des tickets par courriel*

| Champ | Valeur |
|---|---|
| Adresse de réception | `depenses` |
| Domaine de messagerie | votre domaine |
| Expéditeurs autorisés | **Contacts connus uniquement** |

L'adresse complète s'affiche alors en vert, par exemple
`depenses@jscheunersarl.ch`.

> **Sécurité** : « Contacts connus uniquement » est le réglage par défaut
> et le seul recommandé. « Tout le monde » autoriserait n'importe quel
> expéditeur à créer des tickets dans la base, et exposerait l'adresse au
> courrier indésirable.

À l'installation, l'adresse est **vide donc inactive** : la réception ne
démarre qu'après cette configuration explicite.

### Fonctionnement

1. L'employé photographie le ticket et l'envoie à l'adresse.
2. Le ticket est créé aussitôt, à l'état *Brouillon*, marqué « Analyse en
   attente ». Le courriel n'attend pas l'analyse.
3. La tâche planifiée **« Dépenses IA : analyser les tickets reçus »**
   (toutes les 5 minutes) enfile un job par ticket dans `queue_job`
   (canal `root.js_depenses.vision`, puis `.text`) : le ticket passe à
   l'état *Analyse IA* dès l'enfilement.
4. Une fois le job de structuration passé, le ticket passe à
   *À vérifier*. **Aucune comptabilisation automatique n'a lieu** : la
   vérification et la validation restent humaines.

Les pièces trop petites (logos de signature) et les fichiers non
exploitables sont écartés. Après trois échecs, l'analyse est abandonnée
et le ticket signalé ; le bouton **Relancer l'analyse** permet de
réessayer (il repasse par la même file d'attente).

### Dépôt groupé depuis Odoo

Alternative sans courriel : Dépenses IA → Tickets → **Déposer des
tickets**. Une photo par ticket. L'analyse est toujours asynchrone et
enfilée immédiatement à la création des tickets, sans option à cocher.

---

## 8. Tests

```bash
ssh odoo@192.168.1.68 \
  "sudo -u odoo /chemin/odoo-bin -d scheuner_ocr_ia_dev \
   -u js_depenses_ia --test-enable --stop-after-init \
   --log-level=test"
```

40 tests répartis en cinq fichiers : analyse des montants, normalisation,
calcul de TVA, écriture comptable, apprentissage.

---

## 9. Première utilisation

1. Dépenses IA → Tickets → Créer
2. Onglet **Pièces jointes** : ajouter la photo. Depuis un téléphone, le
   sélecteur de fichiers propose directement l'appareil photo.
3. **Analyser avec l'IA** (toujours en arrière-plan : le ticket passe à
   *Analyse IA* puis à *À vérifier* une fois le traitement terminé, à
   suivre dans le fil de discussion), ou saisir les lignes à la main
4. Vérifier le bandeau de contrôle : vert si le total correspond
5. **Vérifier** puis **Comptabiliser**

Les premiers tickets se saisissent principalement à la main : c'est ce
qui constitue la base de règles apprises. Le bénéfice apparaît dès les
tickets suivants du même fournisseur.

---

## Dépannage

**« Le total des lignes ne correspond pas au ticket »**
Comportement attendu. Comparer la photo et les lignes, corriger le
montant fautif. La tolérance ne doit être relevée que pour des arrondis
avérés.

**« Odoo a affecté X au compte d'attente »**
L'écriture générée est déséquilibrée. Vérifier les taxes des lignes :
un compte porte probablement une taxe incohérente avec le montant saisi.

**« Le moteur n'a pas répondu correctement »**
Vérifier qu'Ollama est joignable **depuis le serveur** et non seulement
depuis le poste de développement, et que l'URL configurée n'est pas
`localhost` si le service tourne ailleurs.

**Les tickets restent bloqués à l'état « Analyse IA »**
Le job ne s'exécute pas : vérifier que `queue_job` est bien installé et
que `workers > 0` (voir section 2). Réglages techniques → Tâches en file
d'attente → Tâches permet de consulter l'état de chaque job et son
message d'erreur éventuel.

**Odoo plantait pendant l'analyse (avant la mise en place de `queue_job`)**
C'était `limit_time_real` qui tuait le worker sur un appel synchrone trop
long. L'analyse ne s'exécute plus jamais dans la requête web ; si le
symptôme réapparaît, c'est que `queue_job` n'est pas correctement
installé (voir ci-dessus) et non un problème de performance du moteur IA.

**L'analyse renvoie des montants incohérents**
Vérifier que Tesseract est installé sur le serveur : sans OCR, le modèle
travaille en vision seule et la fiabilité des chiffres chute nettement.

**Aucune règle ne se déclenche**
Les règles ne naissent qu'à la **validation** d'un ticket, pas à sa
simple saisie. Vérifier également que les libellés ne sont pas trop
courts : moins de 4 caractères, aucune règle n'est créée.
