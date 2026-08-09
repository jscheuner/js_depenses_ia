# -*- coding: utf-8 -*-
"""Contrôles de l'écriture produite au grand livre.

Rappel du comportement d'Odoo, vérifié expérimentalement : sur une
écriture de type ``entry``, la balance d'une ligne est toujours traitée
comme une base hors taxe, que la taxe soit « incluse dans le prix » ou
non. Le module doit donc porter le montant hors taxe sur les lignes de
charge, la gestion du TTC étant assurée en amont, sur le ticket.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import JsDepensesCommon


@tagged('post_install', '-at_install')
class TestAccountMove(JsDepensesCommon):

    def test_post_creates_balanced_move(self):
        ticket = self._create_ticket(
            lines=[{'name': "Marchandise", 'price_unit': 100.00,
                    'tax_ids': self.tax_81_incl.ids}],
            total=100.00,
        )
        ticket.action_post()

        self.assertEqual(ticket.state, 'posted')
        move = ticket.move_id
        self.assertTrue(move)
        self.assertEqual(move.move_type, 'entry')
        self.assertEqual(move.journal_id, self.journal)
        # Un ticket « Comptabilisé » doit obligatoirement correspondre à une
        # écriture elle-même validée, pas laissée en brouillon.
        self.assertEqual(move.state, 'posted')
        self.assertEqual(ticket.move_state, 'posted')

        total_debit = sum(move.line_ids.mapped('debit'))
        total_credit = sum(move.line_ids.mapped('credit'))
        self.assertAlmostEqual(total_debit, total_credit, places=2)
        # Le total débité doit correspondre au ticket, au centime.
        self.assertAlmostEqual(total_debit, 100.00, places=2)

    def test_move_line_structure(self):
        """Charge au HT, TVA déductible, contrepartie au TTC."""
        ticket = self._create_ticket(
            lines=[{'name': "Marchandise", 'price_unit': 100.00,
                    'tax_ids': self.tax_81_incl.ids}],
            total=100.00,
        )
        ticket.action_post()
        lines = ticket.move_id.line_ids

        charge = lines.filtered(
            lambda l: l.account_id == self.expense_account)
        self.assertAlmostEqual(charge.debit, 92.51, places=2)

        vat = lines.filtered(lambda l: l.account_id == self.tax_account)
        self.assertAlmostEqual(sum(vat.mapped('debit')), 7.49, places=2)

        counterpart = lines.filtered(
            lambda l: l.account_id == self.counterpart_account)
        self.assertAlmostEqual(counterpart.credit, 100.00, places=2)

    def test_no_suspense_account_line(self):
        """Aucune ligne ne doit atterrir sur le compte d'attente."""
        ticket = self._create_ticket(
            lines=[
                {'name': "Entretien", 'price_unit': 24.50,
                 'account_id': self.expense_account.id,
                 'tax_ids': self.tax_81_incl.ids},
                {'name': "Café", 'price_unit': 12.30,
                 'account_id': self.expense_account_food.id,
                 'tax_ids': self.tax_26_incl.ids},
            ],
            total=36.80,
        )
        ticket.action_post()

        suspense = self.journal.suspense_account_id
        if suspense:
            stray = ticket.move_id.line_ids.filtered(
                lambda l: l.account_id == suspense)
            self.assertFalse(
                stray,
                "Odoo a déversé un écart sur le compte d'attente : "
                "l'écriture ne reflète pas fidèlement le ticket.")

    def test_mixed_rates_move_totals(self):
        ticket = self._create_ticket(
            lines=[
                {'name': "Entretien", 'price_unit': 24.50,
                 'account_id': self.expense_account.id,
                 'tax_ids': self.tax_81_incl.ids},
                {'name': "Café", 'price_unit': 12.30,
                 'account_id': self.expense_account_food.id,
                 'tax_ids': self.tax_26_incl.ids},
            ],
            total=36.80,
        )
        ticket.action_post()
        total_debit = sum(ticket.move_id.line_ids.mapped('debit'))
        self.assertAlmostEqual(total_debit, 36.80, places=2)

    def test_cannot_post_twice(self):
        ticket = self._create_ticket(
            lines=[{'name': "Article", 'price_unit': 50.00,
                    'tax_ids': self.tax_81_incl.ids}],
            total=50.00,
        )
        ticket.action_post()
        with self.assertRaises(UserError):
            ticket.action_post()

    def test_cannot_delete_posted_ticket(self):
        ticket = self._create_ticket(
            lines=[{'name': "Article", 'price_unit': 50.00,
                    'tax_ids': self.tax_81_incl.ids}],
            total=50.00,
        )
        ticket.action_post()
        with self.assertRaises(UserError):
            ticket.unlink()

    def test_post_requires_counterpart(self):
        ticket = self._create_ticket(
            lines=[{'name': "Article", 'price_unit': 50.00,
                    'tax_ids': self.tax_81_incl.ids}],
            total=50.00,
        )
        # Le compte est renseigné automatiquement à la création : on le
        # retire explicitement pour éprouver le contrôle.
        ticket.counterpart_account_id = False
        with self.assertRaises(UserError):
            ticket.action_post()

    def test_move_reference_contains_ticket_number(self):
        ticket = self._create_ticket(
            lines=[{'name': "Article", 'price_unit': 50.00,
                    'tax_ids': self.tax_81_incl.ids}],
            total=50.00,
        )
        ticket.action_post()
        self.assertIn(ticket.name, ticket.move_id.ref or '')

    def test_undo_posting_deletes_move_and_resets_draft(self):
        """Miroir du bouton « Réinitialiser en brouillon » d'une facture
        fournisseur : l'écriture est supprimée et le ticket repasse en
        brouillon."""
        ticket = self._create_ticket(
            lines=[{'name': "Article", 'price_unit': 50.00,
                    'tax_ids': self.tax_81_incl.ids}],
            total=50.00,
        )
        ticket.action_post()
        move = ticket.move_id

        ticket.action_undo_posting()

        self.assertEqual(ticket.state, 'draft')
        self.assertFalse(ticket.move_id)
        self.assertFalse(move.exists())

    def test_undo_posting_requires_posted_state(self):
        ticket = self._create_ticket(
            lines=[{'name': "Article", 'price_unit': 50.00,
                    'tax_ids': self.tax_81_incl.ids}],
            total=50.00,
        )
        with self.assertRaises(UserError):
            ticket.action_undo_posting()

    def test_move_has_backlink_to_ticket(self):
        """L'écriture porte un lien retour vers le ticket qui l'a créée."""
        ticket = self._create_ticket(
            lines=[{'name': "Article", 'price_unit': 50.00,
                    'tax_ids': self.tax_81_incl.ids}],
            total=50.00,
        )
        ticket.action_post()
        self.assertEqual(ticket.move_id.js_depense_ticket_id, ticket)

        action = ticket.move_id.action_view_js_depense_ticket()
        self.assertEqual(action['res_model'], 'js.depense.ticket')
        self.assertEqual(action['res_id'], ticket.id)
