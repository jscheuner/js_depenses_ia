# -*- coding: utf-8 -*-

from odoo import api, fields, models


class JsAiLog(models.Model):
    """Trace d'un échange avec un moteur d'analyse.

    Conserver les allers-retours permet de comprendre a posteriori
    pourquoi une extraction a échoué, et de comparer objectivement les
    moteurs entre eux.
    """

    _name = 'js.ai.log'
    _description = "Échange avec le moteur IA"
    _order = 'create_date desc, id desc'
    _rec_name = 'step'

    ticket_id = fields.Many2one(
        'js.depense.ticket', string="Ticket",
        ondelete='cascade', index=True)
    provider_id = fields.Many2one(
        'js.ai.provider', string="Moteur", ondelete='set null', index=True)
    company_id = fields.Many2one(
        'res.company', string="Société",
        default=lambda self: self.env.company, index=True)

    step = fields.Selection(
        [('vision', "Lecture du ticket (vision)"),
         ('extract', "Extraction du ticket"),
         ('accounts', "Affectation des comptes"),
         ('fix', "Correction d'écart"),
         ('other', "Autre")],
        string="Étape", default='extract', required=True, index=True)
    iteration = fields.Integer(string="Itération", default=1)

    model_name = fields.Char(string="Modèle")
    system_prompt = fields.Text(string="Consigne")
    prompt = fields.Text(string="Requête")
    response = fields.Text(string="Réponse")

    duration = fields.Float(string="Durée (s)", digits=(8, 2))
    success = fields.Boolean(string="Abouti", default=True)
    error_message = fields.Char(string="Erreur")
    image_count = fields.Integer(string="Images transmises", default=0)

    @api.model
    def _record(self, ticket=None, provider=None, step='extract', iteration=1,
                prompt='', system='', response='', duration=0.0,
                success=True, error='', image_count=0, model_name=None):
        """Crée une entrée de journal sans jamais interrompre le traitement.

        ``model_name`` est déterminé par l'appelant lorsqu'il est connu
        (l'étape peut mobiliser le modèle vision ou le modèle texte selon
        qu'une image est transmise) ; à défaut, le modèle vision du
        moteur sert de repli.
        """
        try:
            return self.sudo().create({
                'ticket_id': ticket.id if ticket else False,
                'provider_id': provider.id if provider else False,
                'company_id': (ticket.company_id.id if ticket
                               else self.env.company.id),
                'step': step,
                'iteration': iteration,
                'model_name': model_name or (
                    provider.model_vision if provider else ''),
                'system_prompt': system or '',
                'prompt': (prompt or '')[:60000],
                'response': (response or '')[:60000],
                'duration': duration,
                'success': success,
                'error_message': (error or '')[:255],
                'image_count': image_count,
            })
        except Exception:
            return self.browse()
