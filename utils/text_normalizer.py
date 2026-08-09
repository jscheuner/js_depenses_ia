# -*- coding: utf-8 -*-
"""Normalisation des libellés, socle de l'apprentissage par corrections.

Un ticket ne réimprime jamais deux fois exactement le même libellé : les
quantités, prix, dates, numéros de lot et codes-barres varient. Sans
normalisation, aucune règle apprise ne se redéclencherait.

    "COCA COLA 50CL 2x1.90"   ->  "coca cola cl x"
    "Coca-Cola 50 cl  3x1.90" ->  "coca cola cl x"

Les deux libellés convergent vers la même clé : la règle apprise sur le
premier s'applique au second.
"""

import re
import unicodedata

# Mots vides qui n'apportent aucune information de classification comptable.
_STOPWORDS = {
    'de', 'du', 'des', 'le', 'la', 'les', 'un', 'une', 'et', 'ou', 'a', 'au',
    'aux', 'en', 'pour', 'par', 'sur', 'avec', 'sans', 'chez', 'dans',
    'der', 'die', 'das', 'und', 'fur', 'von', 'mit',
    'the', 'of', 'for', 'with',
    'ttc', 'tva', 'ht', 'chf', 'eur', 'total', 'sous',
    'pce', 'pcs', 'pc', 'kg', 'gr', 'ml', 'cl', 'lt', 'l', 'm', 'cm', 'mm',
    'x', 'ref', 'art', 'no', 'num', 'qte', 'qty',
}

_UNIT_SUFFIX_RE = re.compile(
    r'\b\d+[.,]?\d*\s*(kg|g|gr|mg|l|lt|ml|cl|dl|m|cm|mm|km|pce|pcs|pc|pack|x)\b',
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r'\d+(?:[.,]\d+)?')
_NON_ALNUM_RE = re.compile(r'[^a-z0-9 ]+')
_MULTISPACE_RE = re.compile(r'\s+')

# En deçà de cette longueur, un libellé est trop générique pour fonder une
# règle d'apprentissage fiable.
MIN_KEY_LENGTH = 4


def strip_accents(text):
    """Supprime les diacritiques : « Café » -> « Cafe »."""
    if not text:
        return ''
    decomposed = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in decomposed if not unicodedata.combining(c))


def normalize_label(text, keep_digits=False):
    """Produit la clé de rapprochement d'un libellé de ligne.

    :param keep_digits: conserve les nombres (utile pour comparer des
                        références d'article plutôt que des désignations).
    """
    if not text:
        return ''

    result = strip_accents(str(text)).lower()

    # Les unités de mesure chiffrées sont réduites à leur unité seule,
    # pour que « 50cl » et « 33 cl » convergent.
    result = _UNIT_SUFFIX_RE.sub(lambda m: ' %s ' % m.group(1).lower(), result)

    if not keep_digits:
        result = _NUMBER_RE.sub(' ', result)

    result = _NON_ALNUM_RE.sub(' ', result)
    result = _MULTISPACE_RE.sub(' ', result).strip()

    tokens = [t for t in result.split(' ') if t and t not in _STOPWORDS]
    return ' '.join(tokens)


def normalize_partner(text):
    """Clé de rapprochement d'un nom de fournisseur lu sur un ticket.

    Les enseignes impriment succursales, formes juridiques et numéros de
    filiale de façon très variable :
        "MIGROS M-Budget Lausanne 042"  ->  "migros m budget lausanne"
        "COOP Pronto SA"                ->  "coop pronto"
    """
    if not text:
        return ''

    result = strip_accents(str(text)).lower()
    result = _NON_ALNUM_RE.sub(' ', result)
    result = _NUMBER_RE.sub(' ', result)
    result = _MULTISPACE_RE.sub(' ', result).strip()

    legal_forms = {
        'sa', 'sarl', 'sagl', 'ag', 'gmbh', 'srl', 'spa', 'ltd', 'llc',
        'inc', 'cie', 'co', 'sas', 'eurl', 'snc', 'gbr', 'kg', 'ohg',
    }
    tokens = [t for t in result.split(' ') if t and t not in legal_forms]
    return ' '.join(tokens)


def is_key_usable(key):
    """Un libellé trop court ou purement numérique ne fonde pas de règle."""
    if not key:
        return False
    if len(key) < MIN_KEY_LENGTH:
        return False
    if not any(c.isalpha() for c in key):
        return False
    return True


def token_set(key):
    return set(key.split(' ')) if key else set()


def similarity(key_a, key_b):
    """Indice de Jaccard entre deux clés normalisées, dans ``[0, 1]``.

    Sert de repli lorsqu'aucune correspondance exacte ni « contient » n'est
    trouvée parmi les règles apprises.
    """
    set_a, set_b = token_set(key_a), token_set(key_b)
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    if not intersection:
        return 0.0
    return intersection / float(len(set_a | set_b))
