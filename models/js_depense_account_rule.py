# -*- coding: utf-8 -*-

import logging

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

from ..utils.text_normalizer import (
    normalize_label, is_key_usable, similarity,
)

_logger = logging.getLogger(__name__)

# En deçà de ce seuil de similarité, un rapprochement approximatif est
# jugé trop hasardeux pour être proposé.
SIMILARITY_THRESHOLD = 0.60


class JsDepenseAccountRule(models.Model):
    """Règle apprise : libellé de ligne -> compte comptable.

    Les règles ne sont pas saisies à la main : elles naissent des
    corrections de l'utilisateur et se renforcent à chaque validation.
    Une règle contredite plus souvent qu'elle n'est confirmée se désactive
    d'elle-même.
    """

    _name = 'js.depense.account.rule'
    _description = "Règle d'affectation apprise"
    _order = 'priority desc, confidence desc, hit_count desc, id desc'
    _rec_name = 'label_key'

    company_id = fields.Many2one(
        'res.company', string="Société", required=True, index=True,
        default=lambda self: self.env.company)

    label_key = fields.Char(
        string="Clé de rapprochement", required=True, index=True,
        help="Libellé normalisé : accents, chiffres et mots vides retirés.")
    label_sample = fields.Char(
        string="Exemple de libellé",
        help="Libellé d'origine ayant donné naissance à la règle.")

    partner_id = fields.Many2one(
        'res.partner', string="Fournisseur", index=True, ondelete='cascade',
        help="Laisser vide pour une règle applicable à tous les fournisseurs.")
    is_global = fields.Boolean(
        string="Règle générale", compute='_compute_is_global', store=True)

    account_id = fields.Many2one(
        'account.account', string="Compte", required=True, ondelete='cascade')
    tax_ids = fields.Many2many(
        'account.tax', string="Taxes",
        help="Taxes retenues lors de la correction, lorsqu'elles diffèrent "
             "de celles du compte.")

    hit_count = fields.Integer(
        string="Applications", default=0,
        help="Nombre de fois où la règle a été proposée.")
    confirm_count = fields.Integer(
        string="Confirmations", default=0,
        help="Validations sans retouche : le choix était le bon.")
    reject_count = fields.Integer(
        string="Contredictions", default=0,
        help="Corrections après application : le choix était mauvais.")
    confidence = fields.Float(
        string="Confiance", compute='_compute_confidence',
        store=True, digits=(3, 2))

    priority = fields.Integer(
        string="Priorité", default=0,
        help="Une valeur élevée l'emporte sur les autres règles.")
    active = fields.Boolean(default=True)
    last_used = fields.Datetime(string="Dernière utilisation", readonly=True)

    _sql_constraints = [
        ('unique_rule',
         'UNIQUE(company_id, partner_id, label_key)',
         "Une règle existe déjà pour ce libellé et ce fournisseur."),
    ]

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------
    @api.depends('partner_id')
    def _compute_is_global(self):
        for rule in self:
            rule.is_global = not rule.partner_id

    @api.depends('confirm_count', 'reject_count', 'hit_count')
    def _compute_confidence(self):
        """Estimation prudente du taux de succès.

        On ajoute une observation positive et une négative fictives afin
        qu'une règle vue une seule fois n'affiche pas d'emblée une
        confiance de 100 %.
        """
        for rule in self:
            positive = rule.confirm_count + 1.0
            negative = rule.reject_count + 1.0
            rule.confidence = round(positive / (positive + negative), 2)

    @api.constrains('label_key')
    def _check_label_key(self):
        for rule in self:
            if not is_key_usable(rule.label_key):
                raise ValidationError(_(
                    "Le libellé « %s » est trop court ou trop générique pour "
                    "fonder une règle fiable.", rule.label_key or ''))

    # ------------------------------------------------------------------
    # Recherche
    # ------------------------------------------------------------------
    @api.model
    def _find_best(self, label_key, partner=None, company=None,
                   allow_fuzzy=True):
        """Retourne la meilleure règle applicable, ou un ensemble vide.

        La recherche procède du plus spécifique au plus général :

        1. correspondance exacte pour ce fournisseur ;
        2. correspondance partielle pour ce fournisseur ;
        3. correspondance exacte, toutes enseignes confondues ;
        4. correspondance partielle, toutes enseignes confondues ;
        5. rapprochement approximatif au-dessus du seuil de similarité.
        """
        if not label_key or not is_key_usable(label_key):
            return self.browse()

        company = company or self.env.company
        base = [('company_id', '=', company.id)]

        candidates = [
            base + [('partner_id', '=', partner.id), ('label_key', '=', label_key)]
            if partner else None,
            base + [('partner_id', '=', partner.id),
                    ('label_key', 'in', self._containment_keys(label_key))]
            if partner else None,
            base + [('partner_id', '=', False), ('label_key', '=', label_key)],
            base + [('partner_id', '=', False),
                    ('label_key', 'in', self._containment_keys(label_key))],
        ]

        for domain in candidates:
            if not domain:
                continue
            rule = self.search(domain, limit=1)
            if rule:
                return rule

        if not allow_fuzzy:
            return self.browse()

        return self._find_fuzzy(label_key, partner, company)

    @api.model
    def _containment_keys(self, label_key):
        """Sous-séquences de mots susceptibles de correspondre à une règle.

        « essence sp shell » permet de retrouver une règle enregistrée sur
        « essence » ou « essence sp ».
        """
        tokens = label_key.split(' ')
        keys = []
        for size in range(len(tokens), 0, -1):
            for start in range(0, len(tokens) - size + 1):
                candidate = ' '.join(tokens[start:start + size])
                if is_key_usable(candidate):
                    keys.append(candidate)
        return keys or [label_key]

    @api.model
    def _find_fuzzy(self, label_key, partner=None, company=None):
        """Repli par similarité lexicale."""
        company = company or self.env.company
        domain = [('company_id', '=', company.id)]
        if partner:
            domain.append(('partner_id', 'in', (partner.id, False)))
        else:
            domain.append(('partner_id', '=', False))

        best_rule = self.browse()
        best_score = SIMILARITY_THRESHOLD
        for rule in self.search(domain, limit=500):
            score = similarity(label_key, rule.label_key)
            # À score égal, une règle propre au fournisseur l'emporte.
            if rule.partner_id:
                score += 0.05
            if score > best_score:
                best_score = score
                best_rule = rule
        return best_rule

    # ------------------------------------------------------------------
    # Apprentissage
    # ------------------------------------------------------------------
    @api.model
    def _learn(self, label_key, account, taxes=None, partner=None,
               company=None, was_corrected=True, applied_rule=None):
        """Crée ou renforce une règle. Ne lève jamais d'exception."""
        if not label_key or not account or not is_key_usable(label_key):
            return self.browse()

        company = company or self.env.company
        try:
            domain = [
                ('company_id', '=', company.id),
                ('label_key', '=', label_key),
                ('partner_id', '=', partner.id if partner else False),
            ]
            rule = self.with_context(active_test=False).search(domain, limit=1)

            if rule:
                rule._reinforce(account, taxes, was_corrected)
            else:
                rule = self.create({
                    'company_id': company.id,
                    'label_key': label_key,
                    'partner_id': partner.id if partner else False,
                    'account_id': account.id,
                    'tax_ids': [(6, 0, taxes.ids)] if taxes else False,
                    'hit_count': 1,
                    'confirm_count': 0 if was_corrected else 1,
                    'last_used': fields.Datetime.now(),
                })

            # Une règle qui avait été appliquée puis corrigée est pénalisée.
            if (applied_rule and applied_rule != rule
                    and applied_rule.account_id != account):
                applied_rule._penalize()

            return rule
        except Exception:
            _logger.exception(
                "Apprentissage impossible pour la clé %r", label_key)
            return self.browse()

    def _reinforce(self, account, taxes=None, was_corrected=True):
        """Met à jour une règle existante au vu d'une nouvelle observation."""
        self.ensure_one()
        values = {
            'hit_count': self.hit_count + 1,
            'last_used': fields.Datetime.now(),
        }

        if self.account_id == account:
            values['confirm_count'] = self.confirm_count + 1
            if taxes and set(taxes.ids) != set(self.tax_ids.ids):
                values['tax_ids'] = [(6, 0, taxes.ids)]
        else:
            # La règle proposait un autre compte : elle est contredite.
            values['reject_count'] = self.reject_count + 1
            if self.reject_count + 1 > self.confirm_count:
                # L'utilisateur a plus souvent tort que raison avec cette
                # règle : on bascule sur le nouveau compte.
                values.update({
                    'account_id': account.id,
                    'tax_ids': [(6, 0, taxes.ids)] if taxes else False,
                    'confirm_count': 1,
                    'reject_count': 0,
                })
        self.write(values)
        self._deactivate_if_unreliable()
        return True

    def _penalize(self):
        self.ensure_one()
        self.write({'reject_count': self.reject_count + 1})
        self._deactivate_if_unreliable()
        return True

    def _deactivate_if_unreliable(self):
        """Retire du jeu une règle durablement contredite."""
        self.ensure_one()
        if self.reject_count >= 3 and self.reject_count > self.confirm_count * 2:
            self.active = False
            _logger.info(
                "Règle désactivée (trop d'erreurs) : %s -> %s",
                self.label_key, self.account_id.display_name)
        return True

    def mark_used(self):
        for rule in self:
            rule.last_used = fields.Datetime.now()
        return True

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_reset_counters(self):
        self.write({'hit_count': 0, 'confirm_count': 0, 'reject_count': 0})
        return True

    def action_reactivate(self):
        self.write({'active': True, 'reject_count': 0})
        return True

    @api.model
    def suggest_for_labels(self, labels, partner_id=None, company_id=None):
        """Propositions pour un lot de libellés bruts.

        Utilisé avant l'appel à l'IA : les libellés déjà connus n'ont pas
        besoin d'être soumis au modèle.
        """
        partner = self.env['res.partner'].browse(partner_id) if partner_id \
            else None
        company = self.env['res.company'].browse(company_id) if company_id \
            else self.env.company

        result = {}
        for label in labels or []:
            key = normalize_label(label)
            rule = self._find_best(key, partner, company)
            if rule:
                result[label] = {
                    'account_id': rule.account_id.id,
                    'account_code': rule.account_id.code,
                    'account_name': rule.account_id.name,
                    'tax_ids': rule.tax_ids.ids,
                    'confidence': rule.confidence,
                    'rule_id': rule.id,
                }
        return result
