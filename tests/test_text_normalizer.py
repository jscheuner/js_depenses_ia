# -*- coding: utf-8 -*-
"""Contrôles de la normalisation des libellés.

Sans cette étape, aucune règle apprise ne se redéclencherait : les
tickets ne réimpriment jamais deux fois exactement le même texte.
"""

from odoo.tests.common import TransactionCase
from odoo.tests import tagged

from ..utils.text_normalizer import (
    normalize_label, normalize_partner, similarity, is_key_usable,
)


@tagged('post_install', '-at_install')
class TestTextNormalizer(TransactionCase):

    def test_variants_converge_to_same_key(self):
        """Deux impressions du même article donnent la même clé."""
        first = normalize_label("COCA COLA 50CL 2x1.90")
        second = normalize_label("Coca-Cola 50 cl  3x1.90")
        self.assertEqual(first, second)

    def test_accents_are_removed(self):
        self.assertEqual(
            normalize_label("Café crème"), normalize_label("CAFE CREME"))

    def test_digits_are_dropped_by_default(self):
        key = normalize_label("Vis inox 4x40 mm")
        self.assertNotIn('4', key)
        self.assertNotIn('40', key)

    def test_digits_can_be_kept(self):
        key = normalize_label("Article 12345", keep_digits=True)
        self.assertIn('12345', key)

    def test_stopwords_are_dropped(self):
        key = normalize_label("Boîte de vis pour le chantier")
        self.assertNotIn('de', key.split(' '))
        self.assertNotIn('pour', key.split(' '))
        self.assertIn('boite', key)

    def test_partner_legal_forms_are_dropped(self):
        self.assertEqual(normalize_partner("COOP Pronto SA"), "coop pronto")
        self.assertEqual(normalize_partner("Meyer GmbH"), "meyer")

    def test_partner_branch_numbers_are_dropped(self):
        self.assertEqual(
            normalize_partner("MIGROS M-Budget Lausanne 042"),
            "migros m budget lausanne")

    def test_similarity(self):
        self.assertEqual(similarity("coca cola", "coca cola"), 1.0)
        self.assertGreater(similarity("essence sans plomb", "essence plomb"), 0.5)
        self.assertEqual(similarity("coca cola", "vis inox"), 0.0)

    def test_key_usability(self):
        self.assertFalse(is_key_usable(""))
        self.assertFalse(is_key_usable("ab"))
        self.assertFalse(is_key_usable("123"))
        self.assertTrue(is_key_usable("coca cola"))
