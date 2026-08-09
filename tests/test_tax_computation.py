# -*- coding: utf-8 -*-
"""Contrôles de calcul de TVA, au centime près.

Ces tests sont le garde-fou principal du module : ils vérifient que la
saisie d'un ticket reproduit exactement les montants imprimés, dans les
deux modes (TVA incluse et TVA ajoutée) et lorsque plusieurs taux
coexistent sur un même ticket.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import JsDepensesCommon


@tagged('post_install', '-at_install')
class TestTaxComputation(JsDepensesCommon):

    def test_tax_included_single_line(self):
        """Ticket de 100.00 TTC à 8.1 % : la TVA est extraite du montant."""
        ticket = self._create_ticket(
            lines=[{
                'name': "Marchandise",
                'price_unit': 100.00,
                'tax_ids': self.tax_81_incl.ids,
            }],
            total=100.00,
        )

        line = ticket.line_ids
        self.assertTrue(line.tax_is_included)
        # 100 / 1.081 = 92.5069...  ->  92.51
        self.assertAlmostEqual(line.price_subtotal, 92.51, places=2)
        self.assertAlmostEqual(line.price_tax, 7.49, places=2)
        self.assertAlmostEqual(line.price_total, 100.00, places=2)

        self.assertAlmostEqual(ticket.amount_total, 100.00, places=2)
        self.assertAlmostEqual(ticket.amount_difference, 0.0, places=2)
        self.assertTrue(ticket.is_reconciled)

    def test_tax_excluded_single_line(self):
        """Ligne de 100.00 HT à 8.1 % : la TVA s'ajoute au montant."""
        ticket = self._create_ticket(
            lines=[{
                'name': "Prestation",
                'price_unit': 100.00,
                'tax_ids': self.tax_81_excl.ids,
            }],
            total=108.10,
        )

        line = ticket.line_ids
        self.assertFalse(line.tax_is_included)
        self.assertAlmostEqual(line.price_subtotal, 100.00, places=2)
        self.assertAlmostEqual(line.price_tax, 8.10, places=2)
        self.assertAlmostEqual(line.price_total, 108.10, places=2)
        self.assertTrue(ticket.is_reconciled)

    def test_mixed_vat_rates(self):
        """Ticket mêlant 8.1 % et 2.6 %, cas courant en grande surface."""
        ticket = self._create_ticket(
            lines=[
                {
                    'name': "Produit d'entretien",
                    'price_unit': 24.50,
                    'account_id': self.expense_account.id,
                    'tax_ids': self.tax_81_incl.ids,
                },
                {
                    'name': "Café",
                    'price_unit': 12.30,
                    'account_id': self.expense_account_food.id,
                    'tax_ids': self.tax_26_incl.ids,
                },
            ],
            total=36.80,
        )

        self.assertAlmostEqual(ticket.amount_total, 36.80, places=2)
        self.assertTrue(ticket.is_reconciled)

        # Le récapitulatif doit isoler les deux taux.
        self.assertEqual(len(ticket.tax_line_ids), 2)
        rates = sorted(ticket.tax_line_ids.mapped('tax_rate'))
        self.assertEqual(rates, [2.6, 8.1])

        # La somme des TVA par taux doit égaler la TVA totale.
        self.assertAlmostEqual(
            sum(ticket.tax_line_ids.mapped('tax_amount')),
            ticket.amount_tax, places=2)

    def test_line_without_tax(self):
        """Une ligne sans taxe reste intégralement en hors taxe."""
        ticket = self._create_ticket(
            lines=[{'name': "Timbre", 'price_unit': 15.00, 'tax_ids': []}],
            total=15.00,
        )
        line = ticket.line_ids
        self.assertAlmostEqual(line.price_subtotal, 15.00, places=2)
        self.assertAlmostEqual(line.price_tax, 0.00, places=2)
        self.assertTrue(ticket.is_reconciled)

    def test_negative_line_discount(self):
        """Une remise se saisit comme une ligne négative."""
        ticket = self._create_ticket(
            lines=[
                {'name': "Article", 'price_unit': 50.00,
                 'tax_ids': self.tax_81_incl.ids},
                {'name': "Remise fidélité", 'price_unit': -5.00,
                 'tax_ids': self.tax_81_incl.ids},
            ],
            total=45.00,
        )
        self.assertAlmostEqual(ticket.amount_total, 45.00, places=2)
        self.assertTrue(ticket.is_reconciled)

    def test_account_defines_tax(self):
        """La taxe découle du compte, comme sur une facture fournisseur."""
        ticket = self._create_ticket(total=0.0)
        line = self.env['js.depense.ticket.line'].new({
            'ticket_id': ticket.id,
            'name': "Test",
            'account_id': self.expense_account.id,
        })
        line._onchange_account_id()
        self.assertEqual(line.tax_ids, self.tax_81_incl)

    def test_validation_blocks_on_discrepancy(self):
        """Un écart d'un centime interdit la validation."""
        ticket = self._create_ticket(
            lines=[{'name': "Article", 'price_unit': 100.00,
                    'tax_ids': self.tax_81_incl.ids}],
            total=100.01,
        )
        self.assertFalse(ticket.is_reconciled)
        with self.assertRaises(UserError):
            ticket.action_validate()

    def test_validation_blocks_without_account(self):
        """Aucune validation tant qu'une ligne n'a pas de compte."""
        ticket = self._create_ticket(total=100.00)
        self.env['js.depense.ticket.line'].create({
            'ticket_id': ticket.id,
            'name': "Sans compte",
            'price_unit': 100.00,
        })
        with self.assertRaises(UserError):
            ticket.action_validate()

    def test_validation_without_ticket_total_is_allowed_but_flagged(self):
        """Sans total de référence, la validation reste possible mais le
        contrôle au centime est explicitement signalé comme inactif."""
        ticket = self._create_ticket(
            lines=[{'name': "Article", 'price_unit': 10.00,
                    'tax_ids': self.tax_81_incl.ids}],
            total=0.0,
        )
        ticket.action_validate()
        self.assertEqual(ticket.state, 'validated')
        self.assertFalse(ticket.is_reconciled)

        bodies = ticket.message_ids.mapped('body')
        self.assertTrue(
            any("sans contrôle du total" in (body or '') for body in bodies),
            "L'absence de contrôle doit être tracée dans le fil de discussion.")

    def test_tolerance_allows_rounding_gap(self):
        """Une tolérance explicite autorise un écart d'arrondi."""
        self.company.js_depense_tolerance = 0.05
        ticket = self._create_ticket(
            lines=[{'name': "Article", 'price_unit': 100.00,
                    'tax_ids': self.tax_81_incl.ids}],
            total=100.02,
        )
        ticket.action_validate()
        self.assertEqual(ticket.state, 'validated')

    def test_declared_vat_mismatch_blocks(self):
        """Une TVA imprimée divergente bloque également la validation."""
        ticket = self._create_ticket(
            lines=[{'name': "Article", 'price_unit': 100.00,
                    'tax_ids': self.tax_81_incl.ids}],
            total=100.00,
        )
        ticket.tax_line_ids.write({'tax_amount_ticket': 9.99})
        with self.assertRaises(UserError):
            ticket.action_validate()
