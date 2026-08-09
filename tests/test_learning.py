# -*- coding: utf-8 -*-
"""Contrôles du mécanisme d'apprentissage.

L'objectif poursuivi est que le système se trompe de moins en moins. Ces
tests vérifient que ce n'est pas une intention mais un comportement
observable : une correction effectuée une fois doit s'appliquer seule la
fois suivante.
"""

from odoo.tests import tagged

from .common import JsDepensesCommon


@tagged('post_install', '-at_install')
class TestLearning(JsDepensesCommon):

    def test_correction_is_recorded(self):
        """Toute modification de compte laisse une trace, à la validation."""
        ticket = self._create_ticket(
            lines=[{'name': "Café en grains", 'price_unit': 12.30,
                    'tax_ids': self.tax_81_incl.ids}],
            total=12.30,
        )
        line = ticket.line_ids
        line.write({'account_id': self.expense_account_food.id})

        # Aucune trace tant que le ticket n'est pas validé.
        corrections = self.env['js.depense.correction'].search([
            ('ticket_id', '=', ticket.id),
            ('field_name', '=', 'account_id'),
        ])
        self.assertFalse(corrections)

        ticket.action_validate()

        corrections = self.env['js.depense.correction'].search([
            ('ticket_id', '=', ticket.id),
            ('field_name', '=', 'account_id'),
        ])
        self.assertTrue(corrections)
        self.assertEqual(corrections[0].line_id, line)

    def test_rule_created_on_validation(self):
        """La validation transforme la correction en règle réutilisable."""
        ticket = self._create_ticket(
            lines=[{'name': "Café en grains 500g",
                    'account_id': self.expense_account_food.id,
                    'price_unit': 12.30,
                    'tax_ids': self.tax_26_incl.ids}],
            total=12.30,
        )
        ticket.action_validate()

        rule = self.env['js.depense.account.rule'].search([
            ('company_id', '=', self.company.id),
            ('account_id', '=', self.expense_account_food.id),
        ])
        self.assertTrue(rule, "Aucune règle n'a été apprise.")
        self.assertIn('cafe', rule[0].label_key)

    def test_learned_rule_is_applied_next_time(self):
        """Le libellé déjà corrigé est affecté automatiquement ensuite."""
        first = self._create_ticket(
            lines=[{'name': "Café en grains 500g",
                    'account_id': self.expense_account_food.id,
                    'price_unit': 12.30,
                    'tax_ids': self.tax_26_incl.ids}],
            total=12.30,
        )
        first.action_validate()

        # Nouveau ticket, libellé formulé différemment.
        second = self._create_ticket(total=24.60)
        self.env['js.depense.ticket.line'].create({
            'ticket_id': second.id,
            'name': "CAFE EN GRAINS 1KG",
            'price_unit': 24.60,
        })
        second.action_apply_learned_rules()

        line = second.line_ids
        self.assertEqual(
            line.account_id, self.expense_account_food,
            "La règle apprise n'a pas été réappliquée.")
        self.assertEqual(line.account_source, 'learned')

    def test_rule_confidence_increases_with_confirmations(self):
        """Une règle confirmée gagne en confiance."""
        rule = self.env['js.depense.account.rule']._learn(
            label_key='essence sans plomb',
            account=self.expense_account,
            taxes=self.tax_81_incl,
            company=self.company,
            was_corrected=False,
        )
        initial = rule.confidence
        for _ in range(4):
            rule._reinforce(self.expense_account, self.tax_81_incl,
                            was_corrected=False)
        self.assertGreater(rule.confidence, initial)

    def test_contradicted_rule_switches_account(self):
        """Une règle durablement contredite adopte le nouveau compte."""
        rule_model = self.env['js.depense.account.rule']
        rule = rule_model._learn(
            label_key='produit entretien',
            account=self.expense_account,
            company=self.company,
            was_corrected=True,
        )
        # L'utilisateur choisit systématiquement un autre compte.
        rule._reinforce(self.expense_account_food, was_corrected=True)
        self.assertEqual(rule.account_id, self.expense_account_food)

    def test_unreliable_rule_is_deactivated(self):
        """Une règle trop souvent fausse se retire d'elle-même."""
        rule = self.env['js.depense.account.rule']._learn(
            label_key='libelle ambigu test',
            account=self.expense_account,
            company=self.company,
        )
        rule.write({'reject_count': 5, 'confirm_count': 1})
        rule._deactivate_if_unreliable()
        self.assertFalse(rule.active)

    def test_partner_alias_learned_and_reused(self):
        """L'enseigne imprimée est rattachée durablement au fournisseur."""
        alias_model = self.env['js.depense.partner.alias']
        ticket = self._create_ticket(
            lines=[{'name': "Article", 'price_unit': 10.00,
                    'tax_ids': self.tax_81_incl.ids}],
            total=10.00,
            partner_name_raw="MIGROS M-Budget Lausanne 042",
        )
        ticket.action_validate()

        found = alias_model._find_partner(
            "MIGROS M-Budget Lausanne 042", self.company)
        self.assertEqual(found, self.partner)

        # Une variante de la même enseigne doit aussi être reconnue.
        variant = alias_model._find_partner(
            "Migros M Budget Lausanne", self.company)
        self.assertEqual(variant, self.partner)

    def test_short_label_does_not_create_rule(self):
        """Un libellé trop générique ne fonde aucune règle."""
        rule = self.env['js.depense.account.rule']._learn(
            label_key='ab',
            account=self.expense_account,
            company=self.company,
        )
        self.assertFalse(rule)

    def test_suggest_for_labels(self):
        """L'interface de pré-affectation renvoie les comptes connus."""
        rule_model = self.env['js.depense.account.rule']
        rule_model._learn(
            label_key='essence sans plomb',
            account=self.expense_account,
            company=self.company,
            was_corrected=False,
        )
        result = rule_model.suggest_for_labels(
            ["Essence sans plomb 95"], company_id=self.company.id)
        self.assertIn("Essence sans plomb 95", result)
        self.assertEqual(
            result["Essence sans plomb 95"]['account_id'],
            self.expense_account.id)
