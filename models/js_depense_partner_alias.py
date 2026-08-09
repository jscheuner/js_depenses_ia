# -*- coding: utf-8 -*-

import logging

from odoo import api, fields, models

from ..utils.text_normalizer import normalize_partner, similarity

_logger = logging.getLogger(__name__)

PARTNER_SIMILARITY_THRESHOLD = 0.70


class JsDepensePartnerAlias(models.Model):
    """Correspondance entre une enseigne imprimée et un fournisseur Odoo.

    Les tickets impriment rarement la raison sociale exacte : succursales,
    abréviations et numéros de filiale varient d'un point de vente à
    l'autre. Sans cette table, le fournisseur devrait être re-saisi à
    chaque ticket.

        "MIGROS M-Budget Lausanne 042"  ->  Migros SA
        "COOP Pronto 8734"              ->  Coop Société Coopérative
    """

    _name = 'js.depense.partner.alias'
    _description = "Correspondance enseigne / fournisseur"
    _order = 'hit_count desc, id desc'
    _rec_name = 'alias_raw'

    company_id = fields.Many2one(
        'res.company', string="Société", required=True, index=True,
        default=lambda self: self.env.company)

    alias_raw = fields.Char(
        string="Enseigne lue", required=True,
        help="Texte tel qu'il apparaît sur le ticket.")
    alias_key = fields.Char(
        string="Clé de rapprochement", compute='_compute_alias_key',
        store=True, index=True)

    partner_id = fields.Many2one(
        'res.partner', string="Fournisseur", required=True,
        ondelete='cascade', index=True)

    hit_count = fields.Integer(string="Utilisations", default=0)
    last_used = fields.Datetime(string="Dernière utilisation", readonly=True)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('unique_alias', 'UNIQUE(company_id, alias_key)',
         "Cette enseigne est déjà associée à un fournisseur."),
    ]

    @api.depends('alias_raw')
    def _compute_alias_key(self):
        for alias in self:
            alias.alias_key = normalize_partner(alias.alias_raw)

    # ------------------------------------------------------------------
    # Recherche
    # ------------------------------------------------------------------
    @api.model
    def _find_partner(self, raw_name, company=None):
        """Retrouve le fournisseur associé à une enseigne lue.

        Trois passes : correspondance apprise exacte, similarité sur les
        alias connus, puis recherche directe dans le carnet d'adresses.
        """
        if not raw_name:
            return self.env['res.partner'].browse()

        company = company or self.env.company
        key = normalize_partner(raw_name)
        if not key:
            return self.env['res.partner'].browse()

        alias = self.search([
            ('company_id', '=', company.id),
            ('alias_key', '=', key),
        ], limit=1)
        if alias:
            alias.sudo().write({
                'hit_count': alias.hit_count + 1,
                'last_used': fields.Datetime.now(),
            })
            return alias.partner_id

        best_alias, best_score = None, PARTNER_SIMILARITY_THRESHOLD
        for candidate in self.search(
                [('company_id', '=', company.id)], limit=500):
            score = similarity(key, candidate.alias_key)
            if score > best_score:
                best_score, best_alias = score, candidate
        if best_alias:
            return best_alias.partner_id

        return self._search_partner_directly(key)

    @api.model
    def _search_partner_directly(self, key):
        """Recherche dans le carnet d'adresses, sur la clé normalisée."""
        if not key:
            return self.env['res.partner'].browse()

        partner_model = self.env['res.partner']
        # Le premier mot significatif suffit généralement à isoler l'enseigne.
        first_token = key.split(' ')[0]
        if len(first_token) < 3:
            return partner_model.browse()

        candidates = partner_model.search([
            ('name', 'ilike', first_token),
        ], limit=50)

        best_partner, best_score = partner_model.browse(), 0.75
        for partner in candidates:
            score = similarity(key, normalize_partner(partner.name))
            if score > best_score:
                best_score, best_partner = score, partner
        return best_partner

    # ------------------------------------------------------------------
    # Apprentissage
    # ------------------------------------------------------------------
    @api.model
    def _learn(self, raw_name, partner, company=None):
        """Mémorise l'association enseigne / fournisseur."""
        if not raw_name or not partner:
            return self.browse()

        company = company or self.env.company
        key = normalize_partner(raw_name)
        if not key:
            return self.browse()

        try:
            alias = self.with_context(active_test=False).search([
                ('company_id', '=', company.id),
                ('alias_key', '=', key),
            ], limit=1)

            if alias:
                values = {
                    'hit_count': alias.hit_count + 1,
                    'last_used': fields.Datetime.now(),
                }
                if alias.partner_id != partner:
                    # L'utilisateur a rattaché l'enseigne à un autre
                    # fournisseur : sa dernière décision fait foi.
                    values.update({'partner_id': partner.id, 'hit_count': 1})
                alias.write(values)
                return alias

            return self.create({
                'company_id': company.id,
                'alias_raw': raw_name,
                'partner_id': partner.id,
                'hit_count': 1,
                'last_used': fields.Datetime.now(),
            })
        except Exception:
            _logger.exception(
                "Mémorisation impossible pour l'enseigne %r", raw_name)
            return self.browse()
