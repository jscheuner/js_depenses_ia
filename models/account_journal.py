# -*- coding: utf-8 -*-

from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    js_depense_counterpart_account_id = fields.Many2one(
        'account.account', string="Contrepartie des dépenses",
        help="Compte de passage proposé sur les tickets de dépense "
             "comptabilisés dans ce journal. Prend le pas sur le réglage "
             "de la société.")
    js_depense_ticket_count = fields.Integer(
        string="Tickets de dépense", compute='_compute_js_depense_ticket_count')

    def _compute_js_depense_ticket_count(self):
        ticket_model = self.env['js.depense.ticket']
        grouped = ticket_model._read_group(
            [('journal_id', 'in', self.ids)],
            groupby=['journal_id'],
            aggregates=['__count'],
        )
        mapping = {journal.id: count for journal, count in grouped}
        for journal in self:
            journal.js_depense_ticket_count = mapping.get(journal.id, 0)
