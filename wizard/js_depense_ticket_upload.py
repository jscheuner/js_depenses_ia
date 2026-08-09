# -*- coding: utf-8 -*-

import logging

from odoo import fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class JsDepenseTicketUpload(models.TransientModel):
    """Dépôt groupé de photos ou de scans.

    Permet de déposer d'un coup l'ensemble des tickets d'une journée : un
    ticket est créé par photo, puis analysé automatiquement. La vérification
    et la validation restent à la charge d'une personne.
    """

    _name = 'js.depense.ticket.upload'
    _description = "Dépôt de tickets à analyser"

    attachment_ids = fields.Many2many(
        'ir.attachment', string="Photos et scans",
        help="Une photo par ticket. Les PDF de plusieurs pages sont traités "
             "comme un seul ticket.")
    company_id = fields.Many2one(
        'res.company', string="Société", required=True,
        default=lambda self: self.env.company)
    journal_id = fields.Many2one(
        'account.journal', string="Journal",
        domain="[('company_id', '=', company_id)]",
        default=lambda self: self.env.company.js_depense_journal_id)
    partner_id = fields.Many2one(
        'res.partner', string="Fournisseur",
        help="À renseigner uniquement si toutes les photos proviennent du "
             "même fournisseur. L'analyse le déduit sinon de chaque ticket.")
    ticket_date = fields.Date(
        string="Date par défaut",
        help="Utilisée seulement si l'analyse ne parvient pas à lire la "
             "date sur le ticket.")
    analyze_now = fields.Boolean(
        string="Analyser immédiatement", default=True,
        help="Décochez pour laisser l'analyse se faire en arrière-plan. "
             "Recommandé au-delà de quelques photos, l'analyse pouvant "
             "prendre une trentaine de secondes par ticket.")

    def action_create_tickets(self):
        self.ensure_one()
        if not self.attachment_ids:
            raise UserError(_("Ajoutez au moins une photo ou un scan."))

        tickets = self.env['js.depense.ticket']
        for attachment in self.attachment_ids:
            tickets |= self._create_ticket_from_attachment(attachment)

        if self.analyze_now:
            self._analyze(tickets)

        return self._open_result(tickets)

    def _create_ticket_from_attachment(self, attachment):
        self.ensure_one()
        ticket = self.env['js.depense.ticket'].create({
            'company_id': self.company_id.id,
            'journal_id': self.journal_id.id or False,
            'partner_id': self.partner_id.id or False,
            'ticket_date': self.ticket_date or fields.Date.context_today(self),
            'origin': 'upload',
            'needs_ai_analysis': not self.analyze_now,
        })
        # La pièce est recopiée sur le ticket : l'assistant étant transitoire,
        # ses propres pièces jointes seraient supprimées avec lui.
        copy = attachment.copy({
            'res_model': 'js.depense.ticket',
            'res_id': ticket.id,
        })
        ticket.attachment_ids = [(6, 0, copy.ids)]
        return ticket

    def _analyze(self, tickets):
        """Analyse immédiate, ticket par ticket.

        Un échec sur une photo ne doit pas compromettre les autres : chaque
        ticket est traité indépendamment et l'erreur est consignée sur le
        ticket concerné.
        """
        extractor = self.env['js.ticket.extractor']
        for ticket in tickets:
            try:
                extractor.analyze(ticket)
                ticket.ai_error_message = False
            except Exception as error:
                message = str(error)[:255]
                _logger.warning(
                    "Analyse impossible pour le ticket %s : %s",
                    ticket.name, message)
                ticket.write({
                    'ai_error_message': message,
                    'ai_attempt_count': ticket.ai_attempt_count + 1,
                })
                ticket.message_post(body=_(
                    "<p><b>L'analyse automatique a échoué.</b></p>"
                    "<p>%s</p>", message))
        return True

    def _open_result(self, tickets):
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': _("Tickets à vérifier"),
            'res_model': 'js.depense.ticket',
            'domain': [('id', 'in', tickets.ids)],
            'view_mode': 'list,kanban,form',
            'target': 'current',
        }
        if len(tickets) == 1:
            action.update({
                'view_mode': 'form',
                'res_id': tickets.id,
                'domain': [],
            })
        return action
