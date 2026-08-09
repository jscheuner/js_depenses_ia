# -*- coding: utf-8 -*-

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class JsDepenseCorrection(models.Model):
    """Journal des corrections manuelles.

    Table en ajout seul : chaque divergence entre une valeur proposée
    (par l'IA, par une règle apprise ou par le défaut du compte) et la
    valeur finalement retenue par l'utilisateur y est consignée.

    C'est la matière première de l'apprentissage et, tout autant, la
    source des statistiques permettant de vérifier objectivement que le
    système se trompe de moins en moins.
    """

    _name = 'js.depense.correction'
    _description = "Correction manuelle sur un ticket de dépense"
    _order = 'create_date desc, id desc'
    _rec_name = 'field_label'

    ticket_id = fields.Many2one(
        'js.depense.ticket', string="Ticket",
        required=True, ondelete='cascade', index=True)
    line_id = fields.Many2one(
        'js.depense.ticket.line', string="Ligne",
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='ticket_id.company_id', store=True, index=True)

    field_name = fields.Char(string="Champ", required=True, index=True)
    field_label = fields.Char(string="Libellé du champ")

    source = fields.Selection(
        [('manual', "Saisie manuelle"),
         ('ai', "Proposé par l'IA"),
         ('learned', "Appris de vos corrections"),
         ('account', "Défaut du compte"),
         ('unknown', "Indéterminé")],
        string="Origine de la valeur", default='unknown', index=True,
        help="D'où provenait la valeur avant correction. C'est ce qui permet "
             "de distinguer une erreur de l'IA d'une simple saisie.")

    value_old = fields.Char(string="Valeur initiale")
    value_new = fields.Char(string="Valeur retenue")

    # Contexte de rapprochement, figé au moment de la correction.
    partner_id = fields.Many2one('res.partner', string="Fournisseur", index=True)
    label_raw = fields.Char(string="Libellé d'origine")
    label_normalized = fields.Char(string="Clé de rapprochement", index=True)

    ai_provider_id = fields.Many2one('js.ai.provider', string="Moteur IA")
    ai_model = fields.Char(string="Modèle IA")
    ai_confidence = fields.Float(string="Confiance annoncée", digits=(3, 2))

    user_id = fields.Many2one(
        'res.users', string="Corrigé par", default=lambda self: self.env.user,
        index=True)
    rule_id = fields.Many2one(
        'js.depense.account.rule', string="Règle alimentée",
        ondelete='set null')

    # ------------------------------------------------------------------
    # Enregistrement
    # ------------------------------------------------------------------
    @api.model
    def _values_equal(self, old_value, new_value):
        """Compare une valeur d'enregistrement à une valeur de ``write``."""
        if isinstance(old_value, models.BaseModel):
            old_comparable = old_value.id if len(old_value) <= 1 else old_value.ids
        else:
            old_comparable = old_value

        if old_comparable in (False, None) and new_value in (False, None):
            return True

        if isinstance(old_comparable, float) or isinstance(new_value, float):
            try:
                return abs(float(old_comparable or 0.0)
                           - float(new_value or 0.0)) < 1e-6
            except (TypeError, ValueError):
                return False

        if isinstance(old_comparable, list) or isinstance(new_value, list):
            return str(old_comparable) == str(new_value)

        return old_comparable == new_value

    @api.model
    def _stringify(self, model, field_name, value):
        """Représentation lisible d'une valeur, quel que soit son type."""
        if value in (False, None, ''):
            return ''
        field = model._fields.get(field_name)
        if field and field.type == 'many2one':
            if isinstance(value, models.BaseModel):
                return value.display_name or ''
            try:
                comodel = model.env[field.comodel_name]
                record = comodel.browse(int(value))
                return record.exists().display_name or str(value)
            except (ValueError, TypeError, KeyError):
                return str(value)
        if field and field.type in ('many2many', 'one2many'):
            if isinstance(value, models.BaseModel):
                return ", ".join(value.mapped('display_name'))
            if isinstance(value, (list, tuple)):
                try:
                    comodel = model.env[field.comodel_name]
                    ids = [int(v) for v in value]
                    return ", ".join(
                        comodel.browse(ids).exists().mapped('display_name'))
                except (ValueError, TypeError, KeyError):
                    return str(value)
            return str(value)
        if isinstance(value, models.BaseModel):
            return value.display_name or ''
        return str(value)

    @api.model
    def _comparable_value(self, record, field_name):
        """Représentation stable (types simples, sérialisable en JSON) d'un
        champ, utilisée pour comparer une valeur de référence à la valeur
        finalement retenue.
        """
        field = record._fields[field_name]
        value = record[field_name]
        if field.type == 'many2one':
            return value.id or False
        if field.type in ('many2many', 'one2many'):
            return value.ids
        if field.type == 'datetime':
            return fields.Datetime.to_string(value) if value else False
        if field.type == 'date':
            return fields.Date.to_string(value) if value else False
        return value

    @api.model
    def _record(self, ticket, line, field_name, old_value, new_value,
                source=None):
        """Consigne une correction. Ne lève jamais d'exception.

        L'apprentissage ne doit en aucun cas empêcher un utilisateur
        d'enregistrer son ticket. N'enregistre rien si une correction
        strictement identique existe déjà pour ce ticket.
        """
        try:
            target = line if line is not None else ticket
            field = target._fields.get(field_name)
            label = field.string if field else field_name

            value_old = self._stringify(target, field_name, old_value)
            value_new = self._stringify(target, field_name, new_value)

            if source is None:
                source = 'unknown'
                if line is not None and field_name == 'account_id':
                    source = line.account_source or 'manual'
                elif ticket.ai_raw_json:
                    source = 'ai'

            existing = self.search([
                ('ticket_id', '=', ticket.id),
                ('line_id', '=', line.id if line is not None else False),
                ('field_name', '=', field_name),
                ('value_old', '=', value_old),
                ('value_new', '=', value_new),
            ], limit=1)
            if existing:
                return existing

            values = {
                'ticket_id': ticket.id,
                'line_id': line.id if line is not None else False,
                'field_name': field_name,
                'field_label': label,
                'source': source,
                'value_old': value_old,
                'value_new': value_new,
                'partner_id': ticket.partner_id.id or False,
                'ai_provider_id': ticket.ai_provider_id.id or False,
                'ai_model': ticket.ai_provider_id.model_vision or '',
            }
            if line is not None:
                values.update({
                    'label_raw': line.name or '',
                    'label_normalized': line.name_normalized or '',
                    'ai_confidence': line.ai_confidence or 0.0,
                })
            else:
                values['ai_confidence'] = ticket.ai_confidence or 0.0

            return self.create(values)
        except Exception:
            _logger.exception(
                "Impossible d'enregistrer la correction du champ %s", field_name)
            return self.browse()

    # ------------------------------------------------------------------
    # Statistiques
    # ------------------------------------------------------------------
    @api.model
    def accuracy_report(self, company=None, days=90):
        """Taux d'erreur par champ, sur une période glissante.

        Renvoie une liste de dictionnaires exploitable pour mesurer si la
        qualité des propositions progresse réellement.
        """
        domain = [('source', 'in', ('ai', 'learned'))]
        if company:
            domain.append(('company_id', '=', company.id))
        if days:
            limit_date = fields.Datetime.subtract(
                fields.Datetime.now(), days=days)
            domain.append(('create_date', '>=', limit_date))

        grouped = self._read_group(
            domain,
            groupby=['field_name', 'source'],
            aggregates=['__count'],
        )
        report = {}
        for field_name, source, count in grouped:
            entry = report.setdefault(
                field_name, {'field_name': field_name, 'ai': 0, 'learned': 0})
            entry[source] = entry.get(source, 0) + count
        return list(report.values())
