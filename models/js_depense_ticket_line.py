# -*- coding: utf-8 -*-

import json

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools import float_is_zero

from ..utils.text_normalizer import normalize_label

# Origine de la valeur portée par la ligne, conservée pour mesurer la
# pertinence de l'IA et des règles apprises.
VALUE_SOURCE = [
    ('manual', "Saisie manuelle"),
    ('ai', "Proposé par l'IA"),
    ('learned', "Appris de vos corrections"),
    ('account', "Défaut du compte"),
]


class JsDepenseTicketLine(models.Model):
    _name = 'js.depense.ticket.line'
    _description = "Ligne de ticket de dépense"
    _order = 'ticket_id, sequence, id'

    ticket_id = fields.Many2one(
        'js.depense.ticket', string="Ticket",
        required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(string="Séquence", default=10)

    company_id = fields.Many2one(
        related='ticket_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(
        related='ticket_id.currency_id', store=True)
    partner_id = fields.Many2one(related='ticket_id.partner_id', store=False)
    state = fields.Selection(related='ticket_id.state', store=False)

    name = fields.Char(
        string="Description", required=True,
        help="Libellé tel qu'il figure sur le ticket.")
    name_normalized = fields.Char(
        string="Clé de rapprochement", compute='_compute_name_normalized',
        store=True, index=True,
        help="Libellé réduit à sa forme canonique. C'est cette clé qui permet "
             "de retrouver une correction déjà effectuée sur un libellé "
             "similaire.")

    account_id = fields.Many2one(
        'account.account', string="Compte",
        domain="[('deprecated', '=', False), ('company_ids', 'in', company_id)]",
        help="Compte de charge ou d'immobilisation à débiter.")
    tax_ids = fields.Many2many(
        'account.tax', string="Taxes",
        domain="[('type_tax_use', '=', 'purchase')]")

    quantity = fields.Float(
        string="Quantité", default=1.0, digits='Product Unit of Measure')
    price_unit = fields.Monetary(
        string="Montant unitaire", currency_field='currency_id',
        help="Montant tel qu'imprimé sur le ticket. Si la taxe du compte est "
             "« incluse dans le prix », ce montant s'entend TTC ; sinon il "
             "s'entend hors taxe.")

    tax_is_included = fields.Boolean(
        string="Montant TTC", compute='_compute_tax_is_included', store=True,
        help="Vrai lorsque la taxe appliquée est incluse dans le prix.")
    price_mode_label = fields.Char(
        string="Mode", compute='_compute_tax_is_included')

    price_subtotal = fields.Monetary(
        string="Hors taxe", compute='_compute_amounts',
        store=True, currency_field='currency_id')
    price_tax = fields.Monetary(
        string="TVA", compute='_compute_amounts',
        store=True, currency_field='currency_id')
    price_total = fields.Monetary(
        string="TTC", compute='_compute_amounts',
        store=True, currency_field='currency_id')

    # --- Traçabilité IA et apprentissage -------------------------------
    account_source = fields.Selection(
        VALUE_SOURCE, string="Origine du compte", default='manual')
    account_rule_id = fields.Many2one(
        'js.depense.account.rule', string="Règle appliquée", ondelete='set null')
    ai_confidence = fields.Float(string="Confiance IA", digits=(3, 2))
    ai_account_hint = fields.Char(
        string="Compte suggéré par l'IA",
        help="Ce que l'IA avait proposé, conservé même après correction.")
    correction_baseline_json = fields.Text(
        string="Référence pour l'apprentissage", copy=False,
        help="Valeurs de la ligne telles que proposées (par l'IA, une "
             "règle apprise, ou à la création), avant toute retouche.")

    _sql_constraints = [
        ('quantity_non_zero', 'CHECK(quantity != 0)',
         "La quantité d'une ligne de ticket ne peut pas être nulle."),
    ]

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------
    @api.depends('name')
    def _compute_name_normalized(self):
        for line in self:
            line.name_normalized = normalize_label(line.name)

    @api.depends('tax_ids', 'tax_ids.price_include')
    def _compute_tax_is_included(self):
        for line in self:
            included = any(line.tax_ids.mapped('price_include'))
            line.tax_is_included = included
            if not line.tax_ids:
                line.price_mode_label = _("sans TVA")
            elif included:
                line.price_mode_label = _("TTC")
            else:
                line.price_mode_label = _("HT")

    @api.depends('quantity', 'price_unit', 'tax_ids', 'currency_id',
                 'ticket_id.partner_id')
    def _compute_amounts(self):
        """Délègue intégralement le calcul au moteur natif d'Odoo.

        Aucun calcul de TVA n'est réimplémenté ici : c'est la seule façon
        d'obtenir strictement le même centime qu'une facture fournisseur.
        """
        for line in self:
            currency = line.currency_id or line.company_id.currency_id
            price_unit = line.price_unit or 0.0
            quantity = line.quantity or 0.0

            if not line.tax_ids:
                subtotal = currency.round(price_unit * quantity) if currency \
                    else price_unit * quantity
                line.price_subtotal = subtotal
                line.price_tax = 0.0
                line.price_total = subtotal
                continue

            taxes_res = line.tax_ids.compute_all(
                price_unit,
                currency=currency,
                quantity=quantity,
                product=None,
                partner=line.ticket_id.partner_id or None,
            )
            line.price_subtotal = taxes_res['total_excluded']
            line.price_total = taxes_res['total_included']
            line.price_tax = taxes_res['total_included'] - taxes_res['total_excluded']

    # ------------------------------------------------------------------
    # Interactions
    # ------------------------------------------------------------------
    @api.onchange('account_id')
    def _onchange_account_id(self):
        """La taxe découle du compte, comme sur une facture fournisseur."""
        for line in self:
            if not line.account_id:
                continue
            taxes = line.account_id.tax_ids.filtered(
                lambda t: t.type_tax_use == 'purchase'
                and (not t.company_id or t.company_id == line.company_id))
            if taxes:
                line.tax_ids = [(6, 0, taxes.ids)]
            elif line.account_id.tax_ids:
                # Le compte porte des taxes, mais aucune n'est une taxe
                # d'achat de la société : on ne devine pas.
                line.tax_ids = [(5, 0, 0)]

    @api.constrains('account_id', 'ticket_id')
    def _check_account_company(self):
        for line in self:
            if not line.account_id:
                continue
            companies = line.account_id.company_ids
            if companies and line.company_id not in companies:
                raise ValidationError(_(
                    "Le compte %(account)s n'appartient pas à la société "
                    "%(company)s.",
                    account=line.account_id.display_name,
                    company=line.company_id.display_name))

    # ------------------------------------------------------------------
    # Journalisation des corrections
    # ------------------------------------------------------------------
    # Champs dont l'écart entre valeur de référence et valeur retenue à la
    # validation constitue un enseignement.
    _LEARNING_FIELDS = ('account_id', 'tax_ids', 'price_unit', 'name')

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._seed_correction_baseline()
        return lines

    def write(self, vals):
        # Une correction manuelle du compte invalide l'origine annoncée.
        # Une proposition du système (règle appliquée automatiquement,
        # affectation IA) précise elle-même sa propre origine.
        if 'account_id' in vals and 'account_source' not in vals \
                and not self.env.context.get('js_ai_proposal'):
            vals = dict(vals, account_source='manual')

        result = super().write(vals)

        if self.env.context.get('js_ai_proposal'):
            # Une valeur proposée par le système sert de nouvelle
            # référence : ce n'est pas encore une correction.
            self._update_correction_baseline(vals.keys())

        return result

    def _correction_snapshot(self):
        """Valeurs actuelles des champs suivis, sous forme comparable."""
        self.ensure_one()
        correction_model = self.env['js.depense.correction']
        snapshot = {field_name: correction_model._comparable_value(
            self, field_name) for field_name in self._LEARNING_FIELDS}
        snapshot['account_source'] = self.account_source
        return snapshot

    def _get_correction_baseline(self):
        self.ensure_one()
        if not self.correction_baseline_json:
            return {}
        try:
            return json.loads(self.correction_baseline_json)
        except ValueError:
            return {}

    def _seed_correction_baseline(self):
        """Fixe la référence initiale, à la création de la ligne."""
        for line in self:
            line.correction_baseline_json = json.dumps(
                line._correction_snapshot())

    def _update_correction_baseline(self, field_names):
        """Actualise la référence lorsque le système propose une valeur."""
        touched = set(self._LEARNING_FIELDS) & set(field_names)
        if not touched:
            return
        correction_model = self.env['js.depense.correction']
        for line in self:
            baseline = line._get_correction_baseline()
            for field_name in touched:
                baseline[field_name] = correction_model._comparable_value(
                    line, field_name)
            if 'account_id' in touched:
                baseline['account_source'] = line.account_source
            line.correction_baseline_json = json.dumps(baseline)

    def _log_corrections_on_validate(self):
        """Compare la référence à la valeur retenue et journalise l'écart.

        Ne s'exécute qu'à la validation du ticket.
        """
        self.ensure_one()
        if self.ticket_id.state in ('posted', 'cancel'):
            return
        correction_model = self.env['js.depense.correction'].sudo()
        baseline = self._get_correction_baseline()
        for field_name in self._LEARNING_FIELDS:
            if field_name not in baseline:
                continue
            old_value = baseline[field_name]
            new_value = correction_model._comparable_value(self, field_name)
            if correction_model._values_equal(old_value, new_value):
                continue
            source = None
            if field_name == 'account_id':
                source = baseline.get('account_source') or 'manual'
            correction_model._record(
                ticket=self.ticket_id, line=self, field_name=field_name,
                old_value=old_value, new_value=new_value, source=source)

    def _tax_breakdown(self):
        """Détail par taxe, utilisé pour le récapitulatif TVA du ticket.

        :return: dict {tax_id: {'base': float, 'amount': float}}
        """
        self.ensure_one()
        result = {}
        if not self.tax_ids:
            return result

        currency = self.currency_id or self.company_id.currency_id
        taxes_res = self.tax_ids.compute_all(
            self.price_unit or 0.0,
            currency=currency,
            quantity=self.quantity or 0.0,
            product=None,
            partner=self.ticket_id.partner_id or None,
        )
        for tax_line in taxes_res['taxes']:
            entry = result.setdefault(
                tax_line['id'], {'base': 0.0, 'amount': 0.0})
            entry['base'] += tax_line['base']
            entry['amount'] += tax_line['amount']
        return result

    def _has_amount(self):
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id
        precision = currency.rounding if currency else 0.01
        return not float_is_zero(self.price_total, precision_rounding=precision)

    def name_get(self):
        result = []
        for line in self:
            label = line.name or _("Ligne")
            if line.account_id:
                label = "%s (%s)" % (label, line.account_id.code)
            result.append((line.id, label))
        return result
