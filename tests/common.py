# -*- coding: utf-8 -*-
"""Socle commun aux tests du module.

Le jeu d'essai est construit de toutes pièces plutôt que de s'appuyer sur
la localisation suisse : les tests restent ainsi valables sur n'importe
quelle base, tout en reproduisant fidèlement les taux helvétiques.
"""

from odoo.tests.common import TransactionCase


class JsDepensesCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.company = cls.env.company
        cls.currency = cls.company.currency_id

        cls.tax_account = cls._create_account(
            'TSTTVA', "TVA à récupérer (test)", 'asset_current')
        cls.expense_account = cls._create_account(
            'TST4200', "Achats de marchandises (test)", 'expense')
        cls.expense_account_food = cls._create_account(
            'TST4210', "Denrées alimentaires (test)", 'expense')
        cls.counterpart_account = cls._create_account(
            'TST2000', "Compte de passage dépenses (test)", 'liability_current')

        # Taux suisses en vigueur : normal 8.1 %, réduit 2.6 %.
        cls.tax_81_incl = cls._create_tax("TVA 8.1% incl.", 8.1, True)
        cls.tax_81_excl = cls._create_tax("TVA 8.1% excl.", 8.1, False)
        cls.tax_26_incl = cls._create_tax("TVA 2.6% incl.", 2.6, True)

        cls.expense_account.tax_ids = [(6, 0, cls.tax_81_incl.ids)]
        cls.expense_account_food.tax_ids = [(6, 0, cls.tax_26_incl.ids)]

        cls.journal = cls.env['account.journal'].create({
            'name': "Journal des dépenses (test)",
            'code': 'TSTDP',
            'type': 'general',
            'company_id': cls.company.id,
        })

        cls.company.write({
            'js_depense_journal_id': cls.journal.id,
            'js_depense_counterpart_account_id': cls.counterpart_account.id,
            'js_depense_tolerance': 0.0,
        })

        cls.partner = cls.env['res.partner'].create({
            'name': "Fournisseur de test",
        })

    # ------------------------------------------------------------------
    # Fabriques
    # ------------------------------------------------------------------
    @classmethod
    def _create_account(cls, code, name, account_type):
        return cls.env['account.account'].create({
            'name': name,
            'code': code,
            'account_type': account_type,
            'company_ids': [(6, 0, [cls.company.id])],
        })

    @classmethod
    def _create_tax(cls, name, amount, price_include):
        return cls.env['account.tax'].create({
            'name': name,
            'amount': amount,
            'amount_type': 'percent',
            'type_tax_use': 'purchase',
            'price_include_override':
                'tax_included' if price_include else 'tax_excluded',
            'company_id': cls.company.id,
            'invoice_repartition_line_ids': [
                (0, 0, {
                    'document_type': 'invoice',
                    'repartition_type': 'base',
                    'factor_percent': 100.0,
                }),
                (0, 0, {
                    'document_type': 'invoice',
                    'repartition_type': 'tax',
                    'factor_percent': 100.0,
                    'account_id': cls.tax_account.id,
                }),
            ],
            'refund_repartition_line_ids': [
                (0, 0, {
                    'document_type': 'refund',
                    'repartition_type': 'base',
                    'factor_percent': 100.0,
                }),
                (0, 0, {
                    'document_type': 'refund',
                    'repartition_type': 'tax',
                    'factor_percent': 100.0,
                    'account_id': cls.tax_account.id,
                }),
            ],
        })

    def _create_ticket(self, lines=None, total=None, **kwargs):
        values = {
            'partner_id': self.partner.id,
            'ticket_date': '2026-01-15',
            'description': "Ticket de test",
            'journal_id': self.journal.id,
            'counterpart_account_id': self.counterpart_account.id,
            'company_id': self.company.id,
            'currency_id': self.currency.id,
        }
        values.update(kwargs)
        if total is not None:
            values['amount_total_ticket'] = total

        ticket = self.env['js.depense.ticket'].create(values)

        for line in lines or []:
            self.env['js.depense.ticket.line'].create({
                'ticket_id': ticket.id,
                'name': line.get('name', "Article"),
                'account_id': line.get(
                    'account_id', self.expense_account.id),
                'tax_ids': [(6, 0, line.get('tax_ids', []))],
                'quantity': line.get('quantity', 1.0),
                'price_unit': line['price_unit'],
            })

        ticket.invalidate_recordset()
        ticket._rebuild_tax_lines()
        return ticket
