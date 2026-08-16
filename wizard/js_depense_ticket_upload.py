# -*- coding: utf-8 -*-

from odoo import fields, models, _
from odoo.exceptions import UserError


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
    ai_provider_id = fields.Many2one(
        'js.ai.provider', string="Moteur IA",
        help="Laisser vide pour utiliser le moteur par défaut.")
    ai_provider_count = fields.Integer(
        default=lambda self: self.env['js.ai.provider'].search_count([]))

    def action_create_tickets(self):
        self.ensure_one()
        if not self.attachment_ids:
            raise UserError(_("Ajoutez au moins une photo ou un scan."))

        tickets = self.env['js.depense.ticket']
        for attachment in self.attachment_ids:
            tickets |= self._create_ticket_from_attachment(attachment)

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
            'needs_ai_analysis': True,
            'ai_provider_id': self.ai_provider_id.id or False,
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
        """Met les tickets en file pour l'analyse IA asynchrone, par lots.

        Le découpage en lots et la gestion des échecs (relance ou abandon
        ticket par ticket, sans compromettre les autres) sont entièrement
        pris en charge par ``_start_ai_analysis`` et les jobs qu'elle
        enfile. Voir docs/05_IA.md.
        """
        return tickets._start_ai_analysis()

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
