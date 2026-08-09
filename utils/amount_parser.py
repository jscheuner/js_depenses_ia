# -*- coding: utf-8 -*-
"""Analyse robuste des montants monétaires lus sur un ticket.

Toutes les conversions passent par ``Decimal``. Aucun ``float`` n'est utilisé
avant l'arrondi final, afin d'éviter toute dérive de représentation binaire.

Formats gérés (usage suisse et international) :
    1'234.50    1’234.50    1 234,50    1.234,50    1,234.50
    CHF 12.90   12.90 CHF   -12.90      (12.90)     12.90-
"""

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# Séparateurs de milliers rencontrés sur les tickets suisses :
# apostrophe droite, apostrophe typographique, espace, espace insécable,
# espace fine insécable.
_THOUSANDS_CHARS = "'\u2019\u00a0\u202f "

# Symboles et codes monétaires à retirer avant analyse.
_CURRENCY_TOKENS = (
    'CHF', 'EUR', 'USD', 'FR.', 'FRS', 'SFR', 'TTC', 'TVA', 'HT',
    '€', '$', '£', 'Fr.', 'Fr',
)

_DIGIT_RE = re.compile(r'\d')


class AmountParseError(ValueError):
    """Le texte fourni ne contient pas de montant exploitable."""


def parse_amount(value, default=None):
    """Convertit un texte en ``Decimal``.

    :param value: texte, nombre, ou ``None``
    :param default: valeur retournée si l'analyse échoue ; si ``None``,
                    une ``AmountParseError`` est levée.
    :return: ``Decimal``
    """
    if value is None or value == '':
        if default is not None:
            return _to_decimal(default)
        raise AmountParseError("Montant vide")

    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # Passage par str : Decimal(0.1) vaut 0.1000000000000000055511151231
        return Decimal(str(value))

    text = str(value).strip()
    if not text:
        if default is not None:
            return _to_decimal(default)
        raise AmountParseError("Montant vide")

    try:
        return _parse_text(text)
    except (AmountParseError, InvalidOperation):
        if default is not None:
            return _to_decimal(default)
        raise AmountParseError("Montant illisible : %r" % value)


def _to_decimal(value):
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _parse_text(text):
    negative = False

    # Notation comptable : (12.90) désigne un montant négatif.
    if text.startswith('(') and text.endswith(')'):
        negative = True
        text = text[1:-1]

    # Retrait des libellés monétaires, sans casser les chiffres.
    upper = text.upper()
    for token in _CURRENCY_TOKENS:
        upper = upper.replace(token.upper(), ' ')
    text = upper

    # Signe suffixé : « 12.90- »
    text = text.strip()
    if text.endswith('-'):
        negative = True
        text = text[:-1]
    if text.startswith('-'):
        negative = True
        text = text[1:]
    if text.startswith('+'):
        text = text[1:]

    # On ne conserve que les chiffres et les séparateurs pertinents.
    text = ''.join(c for c in text if c.isdigit() or c in ".,%s" % _THOUSANDS_CHARS)
    text = text.strip(_THOUSANDS_CHARS)

    if not _DIGIT_RE.search(text):
        raise AmountParseError("Aucun chiffre")

    normalized = _normalize_separators(text)

    try:
        result = Decimal(normalized)
    except InvalidOperation:
        raise AmountParseError("Conversion impossible : %r" % text)

    return -result if negative else result


def _normalize_separators(text):
    """Ramène une écriture localisée à la notation ``1234.50``."""
    # Suppression des séparateurs de milliers non ambigus.
    for char in _THOUSANDS_CHARS:
        text = text.replace(char, '')

    has_dot = '.' in text
    has_comma = ',' in text

    if has_dot and has_comma:
        # Le séparateur décimal est le dernier des deux.
        if text.rfind(',') > text.rfind('.'):
            text = text.replace('.', '').replace(',', '.')
        else:
            text = text.replace(',', '')
        return text

    if has_comma:
        # Une virgule suivie d'exactement 3 chiffres et précédée d'au moins un
        # chiffre est très probablement un séparateur de milliers (1,234).
        parts = text.split(',')
        if len(parts) == 2 and len(parts[1]) == 3 and parts[0]:
            return text.replace(',', '')
        return text.replace(',', '.')

    if has_dot:
        parts = text.split('.')
        if len(parts) > 2:
            # 1.234.567 : points de milliers
            return ''.join(parts)
        return text

    return text


def quantize(value, rounding=Decimal('0.01')):
    """Arrondit un ``Decimal`` au pas donné, en arrondi commercial."""
    value = _to_decimal(value)
    rounding = _to_decimal(rounding)
    if rounding <= 0:
        return value
    return (value / rounding).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * rounding


def quantize_currency(value, currency):
    """Arrondit selon la précision d'une devise Odoo (``res.currency``)."""
    if not currency:
        return quantize(value)
    step = Decimal(str(currency.rounding or 0.01))
    return quantize(value, step)


def to_float(value):
    """Conversion finale vers ``float``, pour stockage dans un champ Odoo."""
    return float(_to_decimal(value))


def extract_amounts(text):
    """Retourne tous les montants détectés dans un texte libre (OCR).

    Utilisé pour recouper les valeurs proposées par l'IA avec ce qui est
    réellement imprimé sur le ticket.
    """
    if not text:
        return []
    pattern = re.compile(
        r'(?<![\d.,])'
        r'\d{1,3}(?:[%s]\d{3})*(?:[.,]\d{1,2})?'
        r'(?![\d])' % re.escape(_THOUSANDS_CHARS)
    )
    found = []
    for raw in pattern.findall(text):
        try:
            found.append(parse_amount(raw))
        except AmountParseError:
            continue
    return found
