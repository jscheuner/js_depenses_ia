# -*- coding: utf-8 -*-

from odoo import fields, models, _


class AccountMove(models.Model):
    _inherit = 'account.move'

    # Champ simple, écrit explicitement par js.depense.ticket au moment de
    # la comptabilisation (voir _create_account_move). Un champ calculé sur
    # account.move ne se déclencherait pas lorsque c'est le ticket, sur un
    # autre modèle, qui renseigne son propre move_id après coup.
    js_depense_ticket_id = fields.Many2one(
        'js.depense.ticket', string="Ticket de dépense", readonly=True,
        copy=False, index=True,
        help="Ticket de dépense à l'origine de cette écriture.")

    def action_view_js_depense_ticket(self):
        self.ensure_one()
        if not self.js_depense_ticket_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'name': _("Ticket de dépense"),
            'res_model': 'js.depense.ticket',
            'res_id': self.js_depense_ticket_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
