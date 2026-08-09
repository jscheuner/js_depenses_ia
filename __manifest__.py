# -*- coding: utf-8 -*-
{
    'name': "Dépenses IA",
    'summary': "Saisie automatisée de tickets de dépenses par scan/photo et analyse IA",
    'description': """
Dépenses IA
===========

Saisie de tickets de caisse et notes de frais à partir d'un scan ou d'une photo
prise depuis un téléphone, avec extraction automatique par intelligence
artificielle (Ollama local ou API cloud).

Principes
---------
* Modèles entièrement autonomes : aucun héritage de ``account.move``.
  L'écriture au grand livre n'est produite qu'à la comptabilisation finale.
* Précision au centime : tous les montants sont manipulés en ``Decimal``,
  le calcul de TVA est délégué au moteur natif ``account.tax.compute_all``.
* La TVA est héritée du compte comptable ; si la taxe est « incluse dans le
  prix », la ligne se saisit en TTC, sinon en HT (comportement identique à
  celui d'une facture fournisseur).
* Contrôle bloquant : le total calculé doit correspondre exactement au total
  lu sur le ticket.
* Apprentissage continu : chaque correction manuelle alimente des règles
  réutilisées automatiquement lors des saisies suivantes.
""",
    'author': "J.Scheuner Sàrl",
    'website': "https://www.jscheunersarl.ch",
    'category': 'Accounting/Accounting',
    'version': '18.0.1.0.0',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
        'account',
    ],
    'data': [
        'security/js_depenses_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/js_ai_provider_data.xml',
        'data/js_depense_alias.xml',
        'data/js_depense_cron.xml',
        'views/js_depense_ticket_views.xml',
        'views/js_depense_ticket_upload_views.xml',
        'views/js_depense_ticket_line_views.xml',
        'views/js_depense_correction_views.xml',
        'views/js_depense_account_rule_views.xml',
        'views/js_depense_partner_alias_views.xml',
        'views/js_ai_provider_views.xml',
        'views/js_ai_log_views.xml',
        'views/account_move_views.xml',
        'views/res_config_settings_views.xml',
        'views/js_depenses_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'js_depenses_ia/static/src/scss/js_depenses.scss',
        ],
    },
    'external_dependencies': {
        'python': [],
    },
    'application': True,
    'installable': True,
    'auto_install': False,
}
