# -*- coding: utf-8 -*-

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    js_depense_journal_id = fields.Many2one(
        'account.journal', string="Journal des dépenses",
        domain="[('company_id', '=', id)]",
        help="Journal dans lequel les tickets sont comptabilisés.")
    js_depense_counterpart_account_id = fields.Many2one(
        'account.account', string="Compte de contrepartie",
        help="Compte de passage crédité du total TTC de chaque ticket. "
             "Il est soldé lors du rapprochement avec la banque, la caisse "
             "ou la note de frais.")
    js_depense_rounding_account_id = fields.Many2one(
        'account.account', string="Compte d'écart d'arrondi",
        help="Utilisé pour absorber les écarts de centime tolérés.")
    js_depense_tolerance = fields.Float(
        string="Tolérance d'écart", default=0.0, digits=(12, 2),
        help="Écart maximal admis entre le total des lignes et le total "
             "imprimé sur le ticket. Zéro impose une correspondance exacte, "
             "ce qui est le réglage recommandé.")

    js_depense_ai_provider_id = fields.Many2one(
        'js.ai.provider', string="Moteur IA par défaut")
    js_depense_ai_max_iterations = fields.Integer(
        string="Relances de l'IA", default=2,
        help="Nombre de tentatives supplémentaires lorsque le total extrait "
             "ne correspond pas à la somme des lignes.")
    js_depense_use_ocr = fields.Boolean(
        string="Assistance OCR", default=True,
        help="Transmet au modèle le texte reconnu par Tesseract en plus de "
             "l'image. Les montants sont ainsi lus sur une source "
             "déterministe plutôt que devinés.")
    js_depense_auto_apply_rules = fields.Boolean(
        string="Appliquer les règles apprises", default=True)
    js_depense_ai_batch_size = fields.Integer(
        string="Taille des lots d'analyse", default=10,
        help="Nombre de tickets traités ensemble dans une même phase "
             "(vision puis texte) avant de passer au lot suivant. Un lot "
             "plus grand réduit le nombre de basculements de modèle sur le "
             "GPU mais retarde la validation des premiers tickets du lot.")
