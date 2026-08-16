# -*- coding: utf-8 -*-

import json
import logging
from collections import defaultdict

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero


_logger = logging.getLogger(__name__)

TICKET_STATES = [
    ('draft', "Brouillon"),
    ('analyzing', "Analyse IA"),
    ('to_validate', "À vérifier"),
    ('validated', "Vérifié"),
    ('posted', "Comptabilisé"),
    ('cancel', "Annulé"),
]


class JsDepenseTicket(models.Model):
    """Ticket de dépense.

    Modèle autonome : il ne dérive pas de ``account.move`` et n'en dépend
    pas. L'écriture au grand livre n'est créée qu'à la comptabilisation,
    et référencée par un simple lien.
    """

    _name = 'js.depense.ticket'
    _description = "Ticket de dépense"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'ticket_date desc, id desc'
    _check_company_auto = True

    # ------------------------------------------------------------------
    # Identification
    # ------------------------------------------------------------------
    name = fields.Char(
        string="Numéro", required=True, copy=False, readonly=True,
        default=lambda self: _("Nouveau"), index=True)
    state = fields.Selection(
        TICKET_STATES, string="État", default='draft',
        required=True, copy=False, tracking=True, index=True)
    active = fields.Boolean(default=True)

    company_id = fields.Many2one(
        'res.company', string="Société", required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency', string="Devise", required=True,
        default=lambda self: self.env.company.currency_id)
    user_id = fields.Many2one(
        'res.users', string="Responsable", default=lambda self: self.env.user,
        tracking=True)

    # ------------------------------------------------------------------
    # En-tête
    # ------------------------------------------------------------------
    partner_id = fields.Many2one(
        'res.partner', string="Fournisseur", tracking=True,
        check_company=False, index=True)
    partner_name_raw = fields.Char(
        string="Nom lu sur le ticket",
        help="Enseigne telle qu'imprimée. Conservée pour alimenter la table "
             "de correspondance des fournisseurs.")

    ticket_date = fields.Date(
        string="Date du ticket", required=True, tracking=True,
        default=fields.Date.context_today)
    accounting_date = fields.Date(
        string="Date comptable",
        help="Date de l'écriture. Par défaut, la date du ticket.")
    reference = fields.Char(string="Référence", tracking=True)
    description = fields.Char(string="Description", tracking=True)
    note = fields.Text(string="Notes internes")

    journal_id = fields.Many2one(
        'account.journal', string="Journal", tracking=True,
        check_company=True,
        domain="[('type', 'in', ('general', 'purchase', 'cash', 'bank'))]",
        default=lambda self: self._default_journal_id())
    counterpart_account_id = fields.Many2one(
        'account.account', string="Compte de contrepartie", tracking=True,
        domain="[('deprecated', '=', False), ('company_ids', 'in', company_id)]",
        help="Compte de passage crédité du total TTC. Il sera soldé lors du "
             "rapprochement avec la banque, la caisse ou la note de frais.")
    payment_kind = fields.Selection(
        [('bank', "Compte bancaire"),
         ('cash', "Caisse"),
         ('employee', "Note de frais employé"),
         ('other', "Autre")],
        string="Mode de règlement", default='bank', tracking=True)

    # ------------------------------------------------------------------
    # Contenu
    # ------------------------------------------------------------------
    line_ids = fields.One2many(
        'js.depense.ticket.line', 'ticket_id', string="Lignes",
        copy=True)
    tax_line_ids = fields.One2many(
        'js.depense.ticket.tax', 'ticket_id', string="Récapitulatif TVA",
        readonly=True, copy=False)
    attachment_ids = fields.Many2many(
        'ir.attachment', 'js_depense_ticket_attachment_rel',
        'ticket_id', 'attachment_id', string="Pièces",
        help="Photos ou scans du ticket. Plusieurs pages sont possibles.")
    attachment_count = fields.Integer(
        string="Nombre de pièces", compute='_compute_attachment_count')
    attachment_preview = fields.Image(
        string="Aperçu du ticket", compute='_compute_attachment_preview',
        max_width=1024, max_height=1024,
        help="Aperçu de la première photo ou du premier scan du ticket.")

    # ------------------------------------------------------------------
    # Montants
    # ------------------------------------------------------------------
    amount_untaxed = fields.Monetary(
        string="Total HT", compute='_compute_amounts',
        store=True, currency_field='currency_id', tracking=True)
    amount_tax = fields.Monetary(
        string="Total TVA", compute='_compute_amounts',
        store=True, currency_field='currency_id')
    amount_total = fields.Monetary(
        string="Total calculé", compute='_compute_amounts',
        store=True, currency_field='currency_id', tracking=True)

    amount_total_ticket = fields.Monetary(
        string="Total lu sur le ticket", currency_field='currency_id',
        tracking=True,
        help="Montant total imprimé sur le ticket, extrait automatiquement "
             "lors de l'analyse. Il sert de référence au contrôle : la somme "
             "des lignes doit lui correspondre au centime. Corrigez-le "
             "uniquement si l'analyse a mal lu le ticket.")
    amount_difference = fields.Monetary(
        string="Écart", compute='_compute_amounts',
        store=True, currency_field='currency_id')
    is_reconciled = fields.Boolean(
        string="Montants concordants", compute='_compute_amounts', store=True)
    difference_message = fields.Char(
        string="Contrôle", compute='_compute_amounts')

    # ------------------------------------------------------------------
    # Comptabilisation
    # ------------------------------------------------------------------
    move_id = fields.Many2one(
        'account.move', string="Écriture comptable",
        readonly=True, copy=False, ondelete='set null', index=True)
    move_state = fields.Selection(
        related='move_id.state', string="État de l'écriture", readonly=True)

    # ------------------------------------------------------------------
    # Intelligence artificielle
    # ------------------------------------------------------------------
    origin = fields.Selection(
        [('manual', "Saisie manuelle"),
         ('email', "Reçu par courriel"),
         ('upload', "Photo déposée")],
        string="Provenance", default='manual', readonly=True, index=True)
    needs_ai_analysis = fields.Boolean(
        string="Analyse en attente", default=False, copy=False, index=True,
        help="Le ticket a été reçu et attend son analyse automatique.")
    ai_attempt_count = fields.Integer(
        string="Tentatives d'analyse", default=0, copy=False, readonly=True)
    ai_error_message = fields.Char(
        string="Erreur d'analyse", copy=False, readonly=True)

    ai_provider_id = fields.Many2one(
        'js.ai.provider', string="Moteur IA",
        help="Laisser vide pour utiliser le moteur par défaut.")
    ai_confidence = fields.Float(string="Confiance globale", digits=(3, 2))
    ai_vision_text = fields.Text(
        string="Lecture brute (vision)", copy=False, readonly=True,
        help="Transcription libre du ticket produite par le modèle vision, "
             "avant structuration en JSON par le modèle texte. Vide lorsque "
             "le moteur produit directement le JSON (option « JSON "
             "directement par le modèle vision »). Utile pour comprendre "
             "une erreur de structuration sans consommer à nouveau le "
             "modèle vision.")
    ai_raw_json = fields.Text(string="Réponse brute", copy=False)
    ai_log_ids = fields.One2many(
        'js.ai.log', 'ticket_id', string="Journal des échanges IA",
        readonly=True, copy=False)
    ai_log_count = fields.Integer(compute='_compute_ai_log_count')
    correction_ids = fields.One2many(
        'js.depense.correction', 'ticket_id', string="Corrections",
        readonly=True, copy=False)
    correction_count = fields.Integer(compute='_compute_correction_count')
    correction_baseline_json = fields.Text(
        string="Référence pour l'apprentissage", copy=False,
        help="Valeurs de l'en-tête telles que proposées (par l'IA ou à la "
             "création), avant toute retouche. Comparées à la valeur "
             "retenue lors de la validation, pour détecter une correction.")

    _sql_constraints = [
        ('name_company_uniq', 'UNIQUE(name, company_id)',
         "Ce numéro de ticket existe déjà pour cette société."),
    ]

    # ------------------------------------------------------------------
    # Valeurs par défaut
    # ------------------------------------------------------------------
    @api.model
    def _default_journal_id(self):
        company = self.env.company
        if company.js_depense_journal_id:
            return company.js_depense_journal_id
        return self.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', company.id)],
            limit=1)

    @api.onchange('company_id')
    def _onchange_company_id(self):
        self.currency_id = self.company_id.currency_id
        self.journal_id = self._default_journal_id()
        self.counterpart_account_id = self.company_id.js_depense_counterpart_account_id

    @api.onchange('journal_id')
    def _onchange_journal_id(self):
        if self.journal_id and not self.counterpart_account_id:
            self.counterpart_account_id = (
                self.journal_id.js_depense_counterpart_account_id
                or self.company_id.js_depense_counterpart_account_id)

    @api.onchange('ticket_date')
    def _onchange_ticket_date(self):
        if not self.accounting_date:
            self.accounting_date = self.ticket_date

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------
    @api.depends('attachment_ids')
    def _compute_attachment_count(self):
        for ticket in self:
            ticket.attachment_count = len(ticket.attachment_ids)

    @api.depends('attachment_ids')
    def _compute_attachment_preview(self):
        for ticket in self:
            image = ticket.attachment_ids.filtered(
                lambda att: att.mimetype and att.mimetype.startswith('image/'))[:1]
            ticket.attachment_preview = image.datas if image else False

    @api.depends('ai_log_ids')
    def _compute_ai_log_count(self):
        for ticket in self:
            ticket.ai_log_count = len(ticket.ai_log_ids)

    @api.depends('correction_ids')
    def _compute_correction_count(self):
        for ticket in self:
            ticket.correction_count = len(ticket.correction_ids)

    @api.depends('line_ids.price_subtotal', 'line_ids.price_tax',
                 'line_ids.price_total', 'amount_total_ticket', 'currency_id')
    def _compute_amounts(self):
        for ticket in self:
            currency = ticket.currency_id or ticket.company_id.currency_id
            untaxed = sum(ticket.line_ids.mapped('price_subtotal'))
            tax = sum(ticket.line_ids.mapped('price_tax'))
            total = sum(ticket.line_ids.mapped('price_total'))

            if currency:
                untaxed = currency.round(untaxed)
                tax = currency.round(tax)
                total = currency.round(total)

            ticket.amount_untaxed = untaxed
            ticket.amount_tax = tax
            ticket.amount_total = total

            declared = ticket.amount_total_ticket or 0.0
            difference = total - declared
            if currency:
                difference = currency.round(difference)
            ticket.amount_difference = difference

            precision = currency.rounding if currency else 0.01
            if float_is_zero(declared, precision_rounding=precision):
                # Total non lu : le contrôle au centime ne peut pas s'exercer.
                ticket.is_reconciled = False
                ticket.difference_message = _(
                    "Aucun total de référence n'a été lu sur le ticket : le "
                    "contrôle au centime est inactif.")
            elif float_is_zero(difference, precision_rounding=precision):
                ticket.is_reconciled = True
                ticket.difference_message = _("Le total correspond au ticket.")
            else:
                ticket.is_reconciled = False
                ticket.difference_message = _(
                    "Écart de %(diff)s : les lignes totalisent %(computed)s "
                    "alors que le ticket indique %(declared)s.",
                    diff=ticket._format_amount(difference),
                    computed=ticket._format_amount(total),
                    declared=ticket._format_amount(declared))

    def _format_amount(self, amount):
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id
        symbol = currency.symbol if currency else ''
        return "%s %.2f" % (symbol, amount or 0.0)

    # ------------------------------------------------------------------
    # Récapitulatif TVA
    # ------------------------------------------------------------------
    def _rebuild_tax_lines(self):
        """Reconstruit le récapitulatif TVA à partir des lignes."""
        tax_model = self.env['js.depense.ticket.tax']
        for ticket in self:
            # Les montants déjà saisis pour la TVA imprimée sont préservés.
            declared = {
                line.tax_id.id: line.tax_amount_ticket
                for line in ticket.tax_line_ids
            }
            aggregate = {}
            for line in ticket.line_ids:
                for tax_id, values in line._tax_breakdown().items():
                    entry = aggregate.setdefault(
                        tax_id, {'base': 0.0, 'amount': 0.0})
                    entry['base'] += values['base']
                    entry['amount'] += values['amount']

            ticket.tax_line_ids.unlink()
            currency = ticket.currency_id or ticket.company_id.currency_id
            for tax_id, values in aggregate.items():
                tax_model.create({
                    'ticket_id': ticket.id,
                    'tax_id': tax_id,
                    'base_amount': currency.round(values['base'])
                    if currency else values['base'],
                    'tax_amount': currency.round(values['amount'])
                    if currency else values['amount'],
                    'tax_amount_ticket': declared.get(tax_id, 0.0),
                })
        return True

    def action_rebuild_tax_lines(self):
        self._rebuild_tax_lines()
        return True

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _("Nouveau")) == _("Nouveau"):
                company_id = vals.get('company_id') or self.env.company.id
                seq = self.env['ir.sequence'].with_company(
                    company_id).next_by_code('js.depense.ticket')
                vals['name'] = seq or _("Nouveau")
            if not vals.get('counterpart_account_id'):
                company = self.env['res.company'].browse(
                    vals.get('company_id') or self.env.company.id)
                if company.js_depense_counterpart_account_id:
                    vals['counterpart_account_id'] = \
                        company.js_depense_counterpart_account_id.id
        tickets = super().create(vals_list)
        tickets._rebuild_tax_lines()
        tickets._seed_correction_baseline()
        return tickets

    def write(self, vals):
        result = super().write(vals)
        if self.env.context.get('js_ai_proposal'):
            # Une valeur proposée par le système (IA, règle apprise) sert de
            # nouvelle référence : ce n'est pas encore une correction.
            self._update_correction_baseline(vals.keys())
        if 'line_ids' in vals or 'currency_id' in vals:
            self._rebuild_tax_lines()
        return result

    def unlink(self):
        for ticket in self:
            if ticket.state == 'posted':
                raise UserError(_(
                    "Le ticket %s est comptabilisé. Annulez d'abord "
                    "l'écriture avant de le supprimer.", ticket.name))
        return super().unlink()

    def copy_data(self, default=None):
        default = dict(default or {})
        default.setdefault('name', _("Nouveau"))
        default.setdefault('state', 'draft')
        default.setdefault('move_id', False)
        return super().copy_data(default)

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------
    def action_set_to_validate(self):
        for ticket in self:
            if not ticket.line_ids:
                raise UserError(_(
                    "Le ticket %s ne comporte aucune ligne.", ticket.name))
            ticket._rebuild_tax_lines()
            ticket.state = 'to_validate'
        return True

    def action_validate(self):
        """Vérifie la cohérence puis fige le ticket."""
        for ticket in self:
            ticket._rebuild_tax_lines()
            ticket._check_before_validation()
            ticket.state = 'validated'
            ticket._learn_from_ticket()
        return True

    def action_reset_to_draft(self):
        for ticket in self:
            if ticket.state == 'posted':
                raise UserError(_(
                    "Annulez d'abord l'écriture comptable du ticket %s.",
                    ticket.name))
            ticket.state = 'draft'
        return True

    def action_cancel(self):
        for ticket in self:
            if ticket.move_id and ticket.move_id.state == 'posted':
                raise UserError(_(
                    "L'écriture %s est comptabilisée. Annulez-la depuis la "
                    "comptabilité avant d'annuler le ticket.",
                    ticket.move_id.name))
            ticket.state = 'cancel'
        return True

    def action_undo_posting(self):
        """Annule la comptabilisation, comme le ferait « Réinitialiser en
        brouillon » sur une facture fournisseur.

        L'écriture est repassée en brouillon puis supprimée plutôt que
        laissée y traîner : une fois dépostée, elle n'a plus aucune valeur
        comptable, et la conserver ne ferait que polluer le journal. Le
        ticket peut ensuite être corrigé et recomptabilisé normalement.
        Odoo refuse de son propre chef cette opération si l'écriture a
        déjà consommé un numéro de séquence protégé ou touche un exercice
        clôturé : l'exception remonte alors telle quelle.
        """
        for ticket in self:
            if ticket.state != 'posted':
                raise UserError(_(
                    "Le ticket %s n'est pas comptabilisé.", ticket.name))
            move = ticket.move_id
            move_name = move.display_name if move else ''
            if move.state == 'posted':
                move.button_draft()
            move.unlink()
            ticket.state = 'draft'
            ticket.message_post(body=_(
                "Comptabilisation annulée : l'écriture %s a été supprimée.",
                move_name))
        return True

    # ------------------------------------------------------------------
    # Contrôles
    # ------------------------------------------------------------------
    def _check_before_validation(self):
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id
        precision = currency.rounding if currency else 0.01

        if not self.line_ids:
            raise UserError(_("Le ticket ne comporte aucune ligne."))

        missing = self.line_ids.filtered(lambda l: not l.account_id)
        if missing:
            raise UserError(_(
                "Un compte comptable doit être renseigné sur chaque ligne.\n"
                "Lignes concernées : %s",
                ", ".join(missing.mapped('name'))))

        tolerance = self.company_id.js_depense_tolerance or 0.0
        difference = abs(self.amount_difference)

        if float_is_zero(self.amount_total_ticket,
                         precision_rounding=precision):
            # Aucun total de référence : le rapprochement au centime ne peut
            # pas s'exercer. La validation reste possible, mais le contrôle
            # le plus protecteur du module est alors inactif.
            self.message_post(body=_(
                "<p>Ticket validé <b>sans contrôle du total</b> : aucun "
                "montant de référence n'a été lu sur le ticket. Vérifiez "
                "les lignes avec un soin particulier.</p>"))
            return True

        if float_compare(difference, tolerance,
                         precision_rounding=precision) > 0:
            raise UserError(_(
                "Le total des lignes ne correspond pas au ticket.\n\n"
                "  Lignes    : %(computed)s\n"
                "  Ticket    : %(declared)s\n"
                "  Écart     : %(diff)s\n\n"
                "Corrigez les montants, ou augmentez la tolérance dans les "
                "paramètres si cet écart relève d'un arrondi.",
                computed=self._format_amount(self.amount_total),
                declared=self._format_amount(self.amount_total_ticket),
                diff=self._format_amount(self.amount_difference)))

        # Contrôle taux par taux lorsque le ticket détaille la TVA.
        for tax_line in self.tax_line_ids:
            if not tax_line.tax_amount_ticket:
                continue
            if float_compare(abs(tax_line.difference), tolerance,
                             precision_rounding=precision) > 0:
                raise UserError(_(
                    "La TVA calculée pour « %(tax)s » (%(computed)s) ne "
                    "correspond pas à celle imprimée sur le ticket "
                    "(%(declared)s).",
                    tax=tax_line.tax_id.display_name,
                    computed=self._format_amount(tax_line.tax_amount),
                    declared=self._format_amount(tax_line.tax_amount_ticket)))
        return True

    @api.constrains('amount_total_ticket')
    def _check_amount_total_ticket(self):
        for ticket in self:
            if ticket.amount_total_ticket < 0:
                raise ValidationError(_(
                    "Le total du ticket ne peut pas être négatif."))

    # ------------------------------------------------------------------
    # Comptabilisation
    # ------------------------------------------------------------------
    def action_post(self):
        """Produit l'écriture au grand livre et la valide.

        Le montant porté sur chaque ligne de charge est le **hors taxe**.
        Odoo régénère les lignes de TVA à partir des taxes attachées : sur
        une écriture de type ``entry``, le moteur considère toujours la
        balance comme une base hors taxe, que la taxe soit « incluse » ou
        non. La gestion du prix TTC est donc entièrement assurée en amont,
        au niveau du ticket.

        Un ticket à l'état « Comptabilisé » doit impérativement correspondre
        à une écriture elle-même validée (``account.move.state == 'posted'``) :
        une écriture restée en brouillon n'impacte ni la balance, ni le
        grand livre, ni la déclaration TVA. L'écriture est donc explicitement
        validée ici, et non simplement créée.
        """
        for ticket in self:
            if ticket.state == 'posted':
                raise UserError(_(
                    "Le ticket %s est déjà comptabilisé.", ticket.name))
            if ticket.state != 'validated':
                ticket.action_validate()
            else:
                ticket._check_before_validation()

            ticket._check_posting_setup()
            move = ticket._create_account_move()
            ticket._verify_move(move)
            ticket._post_move(move)

            ticket.move_id = move.id
            # Lien retour, pour que le comptable retrouve le ticket depuis
            # l'écriture elle-même.
            move.js_depense_ticket_id = ticket.id
            ticket.state = 'posted'
            ticket.message_post(body=_(
                "Écriture comptable %s comptabilisée.", move.name or move.id))
        return True

    def _post_move(self, move):
        """Valide l'écriture (``account.move.action_post()``).

        Toute erreur (exercice clôturé, séquence manquante, etc.) annule la
        transaction : le ticket ne peut pas rester à l'état « Comptabilisé »
        avec une écriture non validée.
        """
        self.ensure_one()
        try:
            move.action_post()
        except UserError:
            raise
        except Exception as error:
            _logger.exception(
                "Échec de la validation de l'écriture pour le ticket %s",
                self.name)
            raise UserError(_(
                "L'écriture comptable n'a pas pu être validée pour le "
                "ticket %(ticket)s : %(error)s",
                ticket=self.name, error=error))
        return True

    def _check_posting_setup(self):
        self.ensure_one()
        if not self.journal_id:
            raise UserError(_("Sélectionnez un journal."))
        if not self.counterpart_account_id:
            raise UserError(_(
                "Sélectionnez le compte de contrepartie qui sera crédité du "
                "total du ticket."))
        if self.move_id:
            raise UserError(_(
                "Une écriture est déjà rattachée à ce ticket (%s).",
                self.move_id.display_name))

    def _prepare_move_line_vals(self, line):
        """Ligne de charge, portant le montant hors taxe.

        Le fournisseur du ticket n'est volontairement pas reporté ici : ce
        n'est pas une facture fournisseur, et lier ces écritures à un tiers
        les ferait apparaître dans le grand livre tiers et les rapports par
        partenaire, sans que ce soit l'usage recherché.
        """
        self.ensure_one()
        return {
            'name': line.name or self.description or self.name,
            'account_id': line.account_id.id,
            'debit': line.price_subtotal if line.price_subtotal > 0 else 0.0,
            'credit': -line.price_subtotal if line.price_subtotal < 0 else 0.0,
            'tax_ids': [(6, 0, line.tax_ids.ids)],
        }

    def _prepare_counterpart_line_vals(self):
        """Contrepartie créditée du total TTC."""
        self.ensure_one()
        total = self.amount_total
        label = self.description or self.partner_id.display_name or self.name
        return {
            'name': label,
            'account_id': self.counterpart_account_id.id,
            'debit': -total if total < 0 else 0.0,
            'credit': total if total > 0 else 0.0,
        }

    def _prepare_move_vals(self):
        self.ensure_one()
        lines = [(0, 0, self._prepare_move_line_vals(line))
                 for line in self.line_ids if line.account_id]
        lines.append((0, 0, self._prepare_counterpart_line_vals()))
        values = {
            'move_type': 'entry',
            'journal_id': self.journal_id.id,
            'company_id': self.company_id.id,
            'date': self.accounting_date or self.ticket_date,
            'ref': self._build_move_reference(),
            'line_ids': lines,
        }
        # La devise n'est précisée que si elle diffère de celle de la
        # société : sinon Odoo gère seul les montants en devise et toute
        # valeur imposée risquerait de perturber l'équilibre.
        if self.currency_id and self.currency_id != self.company_id.currency_id:
            values['currency_id'] = self.currency_id.id
        return values

    def _build_move_reference(self):
        self.ensure_one()
        parts = [self.name]
        if self.reference:
            parts.append(self.reference)
        if self.partner_id:
            parts.append(self.partner_id.display_name)
        elif self.partner_name_raw:
            parts.append(self.partner_name_raw)
        return " - ".join(p for p in parts if p)

    def _create_account_move(self):
        self.ensure_one()
        move = self.env['account.move'].with_company(
            self.company_id).create(self._prepare_move_vals())
        self._attach_documents_to_move(move)
        return move

    def _attach_documents_to_move(self, move):
        """Recopie les photos du ticket sur l'écriture, comme justificatif."""
        self.ensure_one()
        for attachment in self.attachment_ids:
            attachment.copy({
                'res_model': 'account.move',
                'res_id': move.id,
            })

    def _absorb_rounding_gap(self, move):
        """Verse un écart d'arrondi de TVA résiduel sur le compte dédié.

        Le total du ticket est calculé en arrondissant la TVA ligne par
        ligne ; Odoo la recalcule de son côté à partir des taxes
        attachées à chaque ligne de l'écriture. Ces deux calculs
        indépendants peuvent différer de quelques centimes sur un ticket à
        nombreuses lignes, sans qu'il s'agisse d'une véritable erreur.

        Sans compte d'écart configuré (réglage « Compte d'écart d'arrondi »)
        ou au-delà de la tolérance configurée, rien n'est absorbé : l'écart
        remonte alors tel quel via les contrôles de ``_verify_move``, pour
        ne jamais masquer silencieusement un vrai déséquilibre.
        """
        self.ensure_one()
        account = self.company_id.js_depense_rounding_account_id
        if not account:
            return
        currency = self.currency_id or self.company_id.currency_id
        precision = currency.rounding if currency else 0.01
        tolerance = self.company_id.js_depense_tolerance or 0.0
        expected = self.amount_total_ticket or self.amount_total

        total_debit = sum(move.line_ids.mapped('debit'))
        total_credit = sum(move.line_ids.mapped('credit'))
        debit_gap = expected - total_debit
        credit_gap = expected - total_credit

        if (float_is_zero(debit_gap, precision_rounding=precision)
                and float_is_zero(credit_gap, precision_rounding=precision)):
            return
        if float_compare(max(abs(debit_gap), abs(credit_gap)), tolerance,
                          precision_rounding=precision) > 0:
            return

        correction_lines = []
        if not float_is_zero(debit_gap, precision_rounding=precision):
            correction_lines.append((0, 0, {
                'name': _("Écart d'arrondi TVA"),
                'account_id': account.id,
                'debit': debit_gap if debit_gap > 0 else 0.0,
                'credit': -debit_gap if debit_gap < 0 else 0.0,
            }))
        if not float_is_zero(credit_gap, precision_rounding=precision):
            correction_lines.append((0, 0, {
                'name': _("Écart d'arrondi TVA"),
                'account_id': account.id,
                'debit': -credit_gap if credit_gap < 0 else 0.0,
                'credit': credit_gap if credit_gap > 0 else 0.0,
            }))
        move.write({'line_ids': correction_lines})

    def _verify_move(self, move):
        """Contrôle a posteriori de l'écriture produite.

        Deux vérifications indissociables :

        * aucune ligne ne doit toucher le compte d'attente du journal —
          Odoo y déverse automatiquement tout déséquilibre ;
        * le total débit doit correspondre exactement au total du ticket.

        En cas d'anomalie, l'exception annule la transaction : ni écriture
        ni ticket comptabilisé ne subsistent.
        """
        self.ensure_one()
        self._absorb_rounding_gap(move)
        currency = self.currency_id or self.company_id.currency_id
        precision = currency.rounding if currency else 0.01

        suspense = self.journal_id.suspense_account_id
        if suspense:
            stray = move.line_ids.filtered(
                lambda l: l.account_id == suspense)
            if stray:
                raise UserError(_(
                    "L'écriture générée est déséquilibrée : Odoo a affecté "
                    "%(amount)s au compte d'attente « %(account)s ». "
                    "Vérifiez les taxes des lignes.",
                    amount=self._format_amount(
                        sum(stray.mapped('debit')) - sum(stray.mapped('credit'))),
                    account=suspense.display_name))

        total_debit = sum(move.line_ids.mapped('debit'))
        total_credit = sum(move.line_ids.mapped('credit'))

        if float_compare(total_debit, total_credit,
                         precision_rounding=precision) != 0:
            raise UserError(_(
                "L'écriture générée n'est pas équilibrée "
                "(débit %(debit)s, crédit %(credit)s).",
                debit=self._format_amount(total_debit),
                credit=self._format_amount(total_credit)))

        expected = self.amount_total_ticket or self.amount_total
        tolerance = self.company_id.js_depense_tolerance or 0.0
        gap = abs(total_debit - expected)

        if float_compare(gap, tolerance, precision_rounding=precision) > 0:
            raise UserError(_(
                "Le total de l'écriture (%(move)s) ne correspond pas au "
                "total du ticket (%(ticket)s).\n\n"
                "Cet écart provient du recalcul de la TVA par Odoo sur la "
                "base hors taxe. Ajustez les montants des lignes.",
                move=self._format_amount(total_debit),
                ticket=self._format_amount(expected)))
        return True

    def action_view_move(self):
        self.ensure_one()
        if not self.move_id:
            raise UserError(_("Aucune écriture n'est rattachée à ce ticket."))
        return {
            'type': 'ir.actions.act_window',
            'name': _("Écriture comptable"),
            'res_model': 'account.move',
            'res_id': self.move_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ------------------------------------------------------------------
    # Apprentissage
    # ------------------------------------------------------------------
    # Champs d'en-tête dont l'écart entre la valeur de référence et la
    # valeur retenue à la validation constitue une correction.
    _CORRECTION_FIELDS = ('partner_id', 'ticket_date', 'reference',
                           'description', 'amount_total_ticket', 'journal_id')

    def _correction_snapshot(self):
        """Valeurs actuelles des champs suivis, sous forme comparable."""
        self.ensure_one()
        correction_model = self.env['js.depense.correction']
        return {field_name: correction_model._comparable_value(
            self, field_name) for field_name in self._CORRECTION_FIELDS}

    def _get_correction_baseline(self):
        self.ensure_one()
        if not self.correction_baseline_json:
            return {}
        try:
            return json.loads(self.correction_baseline_json)
        except ValueError:
            return {}

    def _seed_correction_baseline(self):
        """Fixe la référence initiale, à la création du ticket."""
        for ticket in self:
            ticket.correction_baseline_json = json.dumps(
                ticket._correction_snapshot())

    def _update_correction_baseline(self, field_names):
        """Actualise la référence lorsque le système propose une valeur.

        Une proposition de l'IA (ou d'une règle) n'est pas une correction :
        c'est le nouveau point de comparaison pour la suite.
        """
        touched = set(self._CORRECTION_FIELDS) & set(field_names)
        if not touched:
            return
        correction_model = self.env['js.depense.correction']
        for ticket in self:
            baseline = ticket._get_correction_baseline()
            for field_name in touched:
                baseline[field_name] = correction_model._comparable_value(
                    ticket, field_name)
            ticket.correction_baseline_json = json.dumps(baseline)

    def _log_header_corrections_on_validate(self):
        """Compare la référence à la valeur retenue et journalise l'écart.

        Ne s'exécute qu'à la validation : c'est le seul moment où la
        décision de l'utilisateur est définitive.
        """
        self.ensure_one()
        if self.state == 'posted':
            return
        correction_model = self.env['js.depense.correction'].sudo()
        baseline = self._get_correction_baseline()
        for field_name in self._CORRECTION_FIELDS:
            if field_name not in baseline:
                continue
            old_value = baseline[field_name]
            new_value = correction_model._comparable_value(self, field_name)
            if correction_model._values_equal(old_value, new_value):
                continue
            source = 'ai' if self.ai_raw_json else 'unknown'
            correction_model._record(
                ticket=self, line=None, field_name=field_name,
                old_value=old_value, new_value=new_value, source=source)

    def _learn_from_ticket(self):
        """Consolide les règles à partir du contenu validé.

        Deux signaux sont exploités : la correction explicite d'un compte
        proposé, mais aussi la validation sans retouche, qui confirme la
        pertinence de la proposition.
        """
        self.ensure_one()
        self._log_header_corrections_on_validate()
        rule_model = self.env['js.depense.account.rule'].sudo()
        alias_model = self.env['js.depense.partner.alias'].sudo()

        if self.partner_id and self.partner_name_raw:
            alias_model._learn(
                raw_name=self.partner_name_raw,
                partner=self.partner_id,
                company=self.company_id)

        for line in self.line_ids:
            line._log_corrections_on_validate()
            if not line.account_id or not line.name_normalized:
                continue
            rule_model._learn(
                label_key=line.name_normalized,
                account=line.account_id,
                taxes=line.tax_ids,
                partner=self.partner_id,
                company=self.company_id,
                was_corrected=(line.account_source != 'learned'),
                applied_rule=line.account_rule_id,
            )
        return True

    def action_apply_learned_rules(self):
        """Réapplique les règles connues aux lignes sans compte."""
        rule_model = self.env['js.depense.account.rule']
        applied = 0
        for ticket in self:
            for line in ticket.line_ids:
                if line.account_id:
                    continue
                rule = rule_model._find_best(
                    label_key=line.name_normalized,
                    partner=ticket.partner_id,
                    company=ticket.company_id)
                if not rule:
                    continue
                line.with_context(js_ai_proposal=True).write({
                    'account_id': rule.account_id.id,
                    'tax_ids': [(6, 0, rule.tax_ids.ids)],
                    'account_source': 'learned',
                    'account_rule_id': rule.id,
                })
                applied += 1
            ticket._rebuild_tax_lines()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Règles apprises"),
                'message': _("%s ligne(s) complétée(s).", applied) if applied
                else _("Aucune règle connue ne correspond à ces libellés."),
                'type': 'success' if applied else 'warning',
                'sticky': False,
            },
        }

    # ------------------------------------------------------------------
    # Analyse par intelligence artificielle
    # ------------------------------------------------------------------
    # Nombre de tentatives (vision ou texte) avant abandon définitif d'un
    # ticket : au-delà, seule une relance manuelle (action_retry_analysis)
    # le remet en file. Voir docs/05_IA.md.
    _AI_MAX_ATTEMPTS = 3

    def action_analyze_ai(self):
        """Met le ticket en file pour une analyse IA asynchrone.

        Toujours asynchrone, y compris depuis ce bouton manuel : un appel
        IA peut prendre plusieurs dizaines de secondes avec un modèle
        local, largement au-delà du délai que le worker web accorde à une
        requête (``limit_time_real``). Voir docs/05_IA.md.
        """
        for ticket in self:
            if ticket.state in ('posted', 'cancel'):
                raise UserError(_(
                    "Le ticket %s n'est plus modifiable.", ticket.name))
            if not ticket.attachment_ids:
                raise UserError(_(
                    "Joignez d'abord une photo ou un scan du ticket."))
        self._start_ai_analysis()
        return True

    def action_analyze_ai_notify(self):
        """Variante affichant un accusé de mise en file d'attente.

        L'analyse étant asynchrone, son résultat n'est pas encore connu au
        retour de cet appel : la notification confirme seulement que le
        ticket a été enfilé, pas que l'extraction a réussi.
        """
        self.ensure_one()
        self.action_analyze_ai()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Analyse du ticket"),
                'message': _(
                    "L'analyse a été mise en file d'attente. Le ticket "
                    "passera à « À vérifier » une fois le traitement "
                    "terminé."),
                'type': 'info',
                'sticky': False,
            },
        }

    def _start_ai_analysis(self, batch_size=None):
        """Enfile les tickets pour l'analyse IA, par lots.

        Toujours asynchrone (voir docs/05_IA.md). Les tickets sont
        regroupés par société puis répartis en lots de ``batch_size``
        (taille configurée par société par défaut) afin qu'un lot entier
        passe par la phase vision avant la phase texte, plutôt que
        ticket par ticket : avec un seul GPU local, alterner les deux
        modèles à chaque ticket reviendrait à les charger et décharger en
        boucle.
        """
        tickets = self.filtered(lambda t: t.state not in ('posted', 'cancel'))
        if not tickets:
            return True
        tickets.write({
            'state': 'analyzing',
            'needs_ai_analysis': False,
            'ai_error_message': False,
        })

        by_company = defaultdict(lambda: self.env['js.depense.ticket'])
        for ticket in tickets:
            by_company[ticket.company_id] |= ticket

        for company, company_tickets in by_company.items():
            size = batch_size or company.js_depense_ai_batch_size or 10
            for offset in range(0, len(company_tickets), size):
                batch = company_tickets[offset:offset + size]
                batch.with_delay(
                    description=_(
                        "Analyse IA (vision) — lot de %s ticket(s)",
                        len(batch)),
                )._job_batch_vision()
        return True

    def _job_batch_vision(self):
        """Job ``queue_job`` : phase vision pour un lot de tickets.

        Les tickets dont le moteur ne produit pas directement le JSON
        (``json_from_vision``) sont regroupés dans ``to_structure`` et
        enchaînés dans un unique job texte, pour la même raison que le
        lot vision lui-même : un seul basculement de modèle par lot.
        """
        extractor = self.env['js.ticket.extractor']
        to_structure = self.env['js.depense.ticket']
        for ticket in self:
            try:
                provider = extractor.run_vision_phase(ticket)
                if not provider.json_from_vision:
                    to_structure |= ticket
            except Exception as error:
                ticket._register_ai_failure(error)
        if to_structure:
            to_structure.with_delay(
                description=_(
                    "Analyse IA (texte) — lot de %s ticket(s)",
                    len(to_structure)),
            )._job_batch_text()
        return True

    def _job_batch_text(self):
        """Job ``queue_job`` : phase texte (structuration JSON) d'un lot."""
        extractor = self.env['js.ticket.extractor']
        for ticket in self:
            try:
                extractor.run_text_phase(ticket)
            except Exception as error:
                ticket._register_ai_failure(error)
        return True

    def _register_ai_failure(self, error):
        """Consigne l'échec d'une phase IA et programme, ou non, une relance.

        Appelé pour chaque ticket individuellement, à l'intérieur d'un job
        de lot : l'échec d'un ticket ne doit jamais faire échouer le job
        (et donc les autres tickets du lot). La relance métier se fait via
        le cron périodique, qui repique les tickets dont
        ``needs_ai_analysis`` est repassé à vrai — pas via le
        ``retry_pattern`` du job, réservé aux échecs francs (bug, base de
        données injoignable). Voir docs/05_IA.md.
        """
        self.ensure_one()
        message = str(error)[:255]
        _logger.warning(
            "Analyse automatique en échec pour le ticket %s : %s",
            self.name, message)
        self.ai_attempt_count += 1
        self.write({
            'state': 'draft',
            'ai_error_message': message,
            'needs_ai_analysis': self.ai_attempt_count < self._AI_MAX_ATTEMPTS,
        })
        if self.ai_attempt_count >= self._AI_MAX_ATTEMPTS:
            self.message_post(body=_(
                "<p><b>Analyse automatique abandonnée</b> après "
                "%(count)s tentatives.</p><p>%(error)s</p>"
                "<p>Le ticket peut être analysé manuellement ou saisi à la "
                "main.</p>",
                count=self.ai_attempt_count, error=message))
        return True

    # ------------------------------------------------------------------
    # Réception directe (courriel, dépôt de photo)
    # ------------------------------------------------------------------
    # Types de pièces exploitables par l'analyse : tout le reste (signatures
    # d'entreprise, logos, fichiers bureautiques) est ignoré.
    _ANALYSABLE_MIMETYPES = ('image/', 'application/pdf')
    # En deçà de cette taille, une pièce jointe est presque certainement un
    # logo de signature de courriel et non la photo d'un ticket.
    _MIN_ATTACHMENT_SIZE = 8000

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """Crée un ticket à partir d'un courriel entrant.

        L'utilisateur photographie son ticket avec son téléphone et l'envoie
        à l'adresse dédiée. Le ticket est créé aussitôt, mais l'analyse est
        volontairement différée : faire attendre le serveur de messagerie
        pendant plusieurs dizaines de secondes d'inférence bloquerait la
        réception des courriels suivants.
        """
        values = dict(custom_values or {})
        subject = (msg_dict.get('subject') or '').strip()
        values.setdefault('description', subject or _("Ticket reçu par courriel"))
        values.setdefault('origin', 'email')
        values.setdefault('state', 'draft')
        values.setdefault('needs_ai_analysis', True)

        author = self._find_email_author(msg_dict)
        if author:
            values.setdefault('user_id', author.id)

        ticket = super().message_new(msg_dict, custom_values=values)
        _logger.info(
            "Ticket %s créé depuis le courriel de %s",
            ticket.name, msg_dict.get('email_from'))
        return ticket

    @api.model
    def _find_email_author(self, msg_dict):
        """Retrouve l'utilisateur interne à l'origine du courriel."""
        email = msg_dict.get('email_from') or ''
        if not email:
            return self.env['res.users'].browse()
        parsed = self.env['res.partner']._parse_partner_name(email)
        address = parsed[1] if isinstance(parsed, (list, tuple)) else email
        if not address:
            return self.env['res.users'].browse()
        return self.env['res.users'].sudo().search(
            [('login', '=ilike', address), ('active', '=', True)], limit=1)

    def _collect_linked_attachments(self):
        """Rattache au ticket les pièces reçues par courriel.

        Les pièces d'un courriel entrant sont liées à l'enregistrement par
        Odoo, mais pas au champ ``attachment_ids`` du module : ce report est
        nécessaire pour que l'analyse les prenne en compte.
        """
        attachment_model = self.env['ir.attachment'].sudo()
        for ticket in self:
            linked = attachment_model.search([
                ('res_model', '=', ticket._name),
                ('res_id', '=', ticket.id),
            ])
            usable = linked.filtered(lambda a: ticket._is_analysable(a))
            missing = usable - ticket.attachment_ids
            if missing:
                ticket.attachment_ids = [(4, att.id) for att in missing]
        return True

    def _is_analysable(self, attachment):
        """Écarte les pièces qui ne peuvent pas être un ticket."""
        mimetype = (attachment.mimetype or '').lower()
        if not any(mimetype.startswith(prefix)
                   for prefix in self._ANALYSABLE_MIMETYPES):
            return False
        if mimetype.startswith('image/') \
                and (attachment.file_size or 0) < self._MIN_ATTACHMENT_SIZE:
            return False
        return True

    @api.model
    def _cron_analyze_pending_tickets(self, limit=20):
        """Enfile les tickets reçus, en dehors du flux de messagerie.

        Ne fait qu'enfiler les jobs d'analyse (voir ``_start_ai_analysis``) :
        le traitement proprement dit a lieu dans ``_job_batch_vision`` puis
        ``_job_batch_text``, exécutés par le(s) worker(s) ``queue_job`` en
        respectant le canal exclusif du GPU. Voir docs/05_IA.md.
        """
        pending = self.search([
            ('needs_ai_analysis', '=', True),
            ('state', 'in', ('draft', 'analyzing')),
            ('ai_attempt_count', '<', self._AI_MAX_ATTEMPTS),
        ], limit=limit)
        if not pending:
            return True

        pending._collect_linked_attachments()
        without_attachment = pending.filtered(lambda t: not t.attachment_ids)
        if without_attachment:
            # Courriel sans pièce exploitable : inutile d'insister.
            without_attachment.write({
                'needs_ai_analysis': False,
                'ai_error_message': _(
                    "Aucune photo ou aucun PDF exploitable n'était joint "
                    "au message."),
            })
            for ticket in without_attachment:
                ticket.message_post(body=_(
                    "<p>Analyse abandonnée : le message ne contenait "
                    "aucune pièce exploitable.</p>"))

        (pending - without_attachment)._start_ai_analysis()
        return True

    def action_retry_analysis(self):
        """Relance une analyse abandonnée."""
        self.write({
            'state': 'draft',
            'needs_ai_analysis': True,
            'ai_attempt_count': 0,
            'ai_error_message': False,
        })
        return True

    # ------------------------------------------------------------------
    # Actions annexes
    # ------------------------------------------------------------------
    def action_open_corrections(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Corrections du ticket"),
            'res_model': 'js.depense.correction',
            'view_mode': 'list,form',
            'domain': [('ticket_id', '=', self.id)],
            'context': {'create': False},
        }

    def action_open_ai_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Échanges avec l'IA"),
            'res_model': 'js.ai.log',
            'view_mode': 'list,form',
            'domain': [('ticket_id', '=', self.id)],
            'context': {'create': False},
        }
