# -*- coding: utf-8 -*-
"""Contrôles de l'analyse des montants.

Les formats retenus sont ceux réellement rencontrés sur les tickets
suisses, où l'apostrophe sert de séparateur de milliers.
"""

from decimal import Decimal

from odoo.tests.common import TransactionCase
from odoo.tests import tagged

from ..utils.amount_parser import (
    parse_amount, quantize, extract_amounts, AmountParseError,
)


@tagged('post_install', '-at_install')
class TestAmountParser(TransactionCase):

    def test_swiss_thousands_separator(self):
        self.assertEqual(parse_amount("1'234.50"), Decimal('1234.50'))
        self.assertEqual(parse_amount("1’999.95"), Decimal('1999.95'))
        self.assertEqual(parse_amount("12'345'678.90"), Decimal('12345678.90'))

    def test_international_formats(self):
        self.assertEqual(parse_amount("1 234,50"), Decimal('1234.50'))
        self.assertEqual(parse_amount("1.234,50"), Decimal('1234.50'))
        self.assertEqual(parse_amount("1,234.50"), Decimal('1234.50'))

    def test_currency_tokens_are_ignored(self):
        self.assertEqual(parse_amount("CHF 12.90"), Decimal('12.90'))
        self.assertEqual(parse_amount("12.90 CHF"), Decimal('12.90'))
        self.assertEqual(parse_amount("Fr. 8.50"), Decimal('8.50'))

    def test_negative_notations(self):
        self.assertEqual(parse_amount("-12.90"), Decimal('-12.90'))
        self.assertEqual(parse_amount("(12.90)"), Decimal('-12.90'))
        self.assertEqual(parse_amount("12.90-"), Decimal('-12.90'))

    def test_float_precision_is_preserved(self):
        """Le passage par une chaîne évite toute dérive binaire."""
        self.assertEqual(parse_amount(0.1), Decimal('0.1'))
        self.assertEqual(parse_amount(8.1), Decimal('8.1'))

    def test_invalid_raises_or_defaults(self):
        with self.assertRaises(AmountParseError):
            parse_amount("illisible")
        self.assertEqual(parse_amount("illisible", default=0), Decimal('0'))
        self.assertEqual(parse_amount(None, default=0), Decimal('0'))

    def test_quantize_uses_commercial_rounding(self):
        self.assertEqual(quantize(Decimal('92.505')), Decimal('92.51'))
        self.assertEqual(quantize(Decimal('92.504')), Decimal('92.50'))
        # Arrondi au 5 centimes, usage des paiements en espèces.
        self.assertEqual(
            quantize(Decimal('12.93'), Decimal('0.05')), Decimal('12.95'))

    def test_extract_amounts_from_ocr_text(self):
        text = "Total CHF 45.60 dont TVA 8.1% soit 3.42 sur 1'234.50"
        amounts = extract_amounts(text)
        self.assertIn(Decimal('45.60'), amounts)
        self.assertIn(Decimal('3.42'), amounts)
        self.assertIn(Decimal('1234.50'), amounts)
