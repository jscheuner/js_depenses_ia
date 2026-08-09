# -*- coding: utf-8 -*-

from odoo import api, fields, models


class JsDepenseTicketTax(models.Model):
    """Récapitulatif de TVA par taux, pour un ticket.

    Un même ticket peut mêler plusieurs taux (8.1 % sur la marchandise,
    2.6 % sur l'alimentaire). Cette table permet de confronter, taux par
    taux, la TVA calculée par Odoo à la TVA effectivement imprimée sur le
    ticket : c'est le contrôle le plus fin dont dispose l'utilisateur.
    """

    _name = 'js.depense.ticket.tax'
    _description = "Récapitulatif TVA d'un ticket de dépense"
    _order = 'ticket_id, tax_rate desc, id'

    ticket_id = fields.Many2one(
        'js.depense.ticket', string="Ticket",
        required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='ticket_id.company_id', store=True)
    currency_id = fields.Many2one(related='ticket_id.currency_id', store=True)

    tax_id = fields.Many2one(
        'account.tax', string="Taxe", required=True, ondelete='restrict')
    tax_rate = fields.Float(
        string="Taux", related='tax_id.amount', store=True, digits=(5, 2))
    price_include = fields.Boolean(
        string="Incluse", related='tax_id.price_include', store=True)

    base_amount = fields.Monetary(
        string="Base HT", currency_field='currency_id',
        help="Base de calcul obtenue à partir des lignes du ticket.")
    tax_amount = fields.Monetary(
        string="TVA calculée", currency_field='currency_id')

    tax_amount_ticket = fields.Monetary(
        string="TVA imprimée", currency_field='currency_id',
        help="Montant de TVA lu sur le ticket pour ce taux. Laisser à zéro "
             "si le ticket ne détaille pas la TVA par taux.")
    difference = fields.Monetary(
        string="Écart", compute='_compute_difference',
        store=True, currency_field='currency_id')
    has_difference = fields.Boolean(
        string="Écart détecté", compute='_compute_difference', store=True)

    @api.depends('tax_amount', 'tax_amount_ticket', 'currency_id')
    def _compute_difference(self):
        for record in self:
            currency = record.currency_id or record.company_id.currency_id
            if not record.tax_amount_ticket:
                # Le ticket ne détaille pas ce taux : aucun écart opposable.
                record.difference = 0.0
                record.has_difference = False
                continue
            diff = record.tax_amount - record.tax_amount_ticket
            record.difference = currency.round(diff) if currency else diff
            record.has_difference = bool(
                currency.compare_amounts(record.difference, 0.0)
                if currency else record.difference)

    def name_get(self):
        return [(rec.id, "%s : %s" % (
            rec.tax_id.display_name, rec.tax_amount)) for rec in self]
