# -*- coding: utf-8 -*-

from odoo import api, fields, models

ALIAS_XMLID = 'js_depenses_ia.mail_alias_depense_ticket'


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    js_depense_journal_id = fields.Many2one(
        related='company_id.js_depense_journal_id', readonly=False)
    js_depense_counterpart_account_id = fields.Many2one(
        related='company_id.js_depense_counterpart_account_id', readonly=False)
    js_depense_rounding_account_id = fields.Many2one(
        related='company_id.js_depense_rounding_account_id', readonly=False)
    js_depense_tolerance = fields.Float(
        related='company_id.js_depense_tolerance', readonly=False)

    js_depense_ai_provider_id = fields.Many2one(
        related='company_id.js_depense_ai_provider_id', readonly=False)
    js_depense_ai_max_iterations = fields.Integer(
        related='company_id.js_depense_ai_max_iterations', readonly=False)
    js_depense_use_ocr = fields.Boolean(
        related='company_id.js_depense_use_ocr', readonly=False)
    js_depense_auto_apply_rules = fields.Boolean(
        related='company_id.js_depense_auto_apply_rules', readonly=False)
    js_depense_ai_batch_size = fields.Integer(
        related='company_id.js_depense_ai_batch_size', readonly=False)

    # ------------------------------------------------------------------
    # Adresse de réception des tickets
    # ------------------------------------------------------------------
    js_depense_alias_name = fields.Char(
        string="Adresse de réception",
        compute='_compute_alias_settings', inverse='_inverse_alias_name',
        help="Partie située avant l'arobase. Les tickets photographiés et "
             "envoyés à cette adresse créent automatiquement un ticket, "
             "analysé puis soumis à vérification. Laisser vide désactive "
             "la réception par courriel.")
    js_depense_alias_domain_id = fields.Many2one(
        'mail.alias.domain', string="Domaine de messagerie",
        compute='_compute_alias_settings', inverse='_inverse_alias_domain',
        help="Domaine utilisé pour l'adresse de réception.")
    js_depense_alias_full = fields.Char(
        string="Adresse complète", compute='_compute_alias_settings')
    js_depense_alias_contact = fields.Selection(
        [('everyone', "Tout le monde"),
         ('partners', "Contacts connus uniquement"),
         ('followers', "Abonnés du document uniquement")],
        string="Expéditeurs autorisés",
        compute='_compute_alias_settings', inverse='_inverse_alias_contact',
        help="« Tout le monde » permet à n'importe quel expéditeur de créer "
             "un ticket : à n'utiliser que sur une adresse non publiée.")

    def _get_alias(self):
        return self.env.ref(ALIAS_XMLID, raise_if_not_found=False)

    @api.depends_context('company')
    def _compute_alias_settings(self):
        alias = self._get_alias()
        for setting in self:
            setting.js_depense_alias_name = alias.alias_name if alias else False
            setting.js_depense_alias_domain_id = (
                alias.alias_domain_id.id if alias and alias.alias_domain_id
                else False)
            setting.js_depense_alias_contact = (
                alias.alias_contact if alias else False)
            if alias and alias.alias_name and alias.alias_domain_id:
                setting.js_depense_alias_full = "%s@%s" % (
                    alias.alias_name, alias.alias_domain_id.name)
            else:
                setting.js_depense_alias_full = False

    def _inverse_alias_name(self):
        alias = self._get_alias()
        if not alias:
            return
        for setting in self:
            alias.sudo().alias_name = setting.js_depense_alias_name or False

    def _inverse_alias_domain(self):
        alias = self._get_alias()
        if not alias:
            return
        for setting in self:
            alias.sudo().alias_domain_id = \
                setting.js_depense_alias_domain_id.id or False

    def _inverse_alias_contact(self):
        alias = self._get_alias()
        if not alias:
            return
        for setting in self:
            if setting.js_depense_alias_contact:
                alias.sudo().alias_contact = setting.js_depense_alias_contact
