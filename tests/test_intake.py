# -*- coding: utf-8 -*-
"""Contrôles de la réception des tickets : courriel entrant, dépôt groupé
et analyse différée.

L'exigence de fond est qu'aucun ticket reçu ne se comptabilise tout seul :
l'analyse pré-remplit, un humain vérifie et valide.
"""

import base64

from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests import tagged

from .common import JsDepensesCommon


def _fake_png(size=20000):
    """Contenu binaire crédible : en-tête PNG puis remplissage, afin de
    dépasser le seuil au-delà duquel une pièce est jugée exploitable."""
    return b'\x89PNG\r\n\x1a\n' + b'\x00' * size


@tagged('post_install', '-at_install')
class TestIntake(JsDepensesCommon):

    def _attach(self, ticket, name="ticket.png", mimetype="image/png",
                content=None):
        return self.env['ir.attachment'].create({
            'name': name,
            'mimetype': mimetype,
            'datas': base64.b64encode(content or _fake_png()),
            'res_model': 'js.depense.ticket',
            'res_id': ticket.id,
        })

    # ------------------------------------------------------------------
    # Courriel entrant
    # ------------------------------------------------------------------
    def test_email_creates_ticket_awaiting_analysis(self):
        ticket = self.env['js.depense.ticket'].message_new({
            'subject': "Ticket essence",
            'email_from': "employe@example.com",
        })
        self.assertEqual(ticket.origin, 'email')
        self.assertEqual(ticket.state, 'draft')
        self.assertTrue(ticket.needs_ai_analysis)
        self.assertEqual(ticket.description, "Ticket essence")

    def test_email_attachments_are_collected(self):
        ticket = self.env['js.depense.ticket'].message_new({
            'subject': "Ticket",
            'email_from': "employe@example.com",
        })
        self._attach(ticket)
        ticket._collect_linked_attachments()
        self.assertEqual(len(ticket.attachment_ids), 1)

    def test_signature_logos_are_ignored(self):
        """Un petit logo de signature ne doit pas être pris pour un ticket."""
        ticket = self.env['js.depense.ticket'].message_new({
            'subject': "Ticket",
            'email_from': "employe@example.com",
        })
        self._attach(ticket, name="logo.png", content=_fake_png(size=200))
        self._attach(ticket, name="contrat.docx",
                     mimetype="application/vnd.openxmlformats-officedocument"
                              ".wordprocessingml.document")
        ticket._collect_linked_attachments()
        self.assertFalse(ticket.attachment_ids)

    # ------------------------------------------------------------------
    # Analyse différée
    # ------------------------------------------------------------------
    def test_cron_analyses_pending_ticket(self):
        """Le cron enfile un job ; l'exécuter le mène à « À vérifier ».

        L'analyse étant asynchrone (voir docs/05_IA.md), le cron ne fait
        qu'enfiler ``_job_batch_vision`` via ``with_delay()`` : dans une
        transaction de test, sans JobRunner réel, ce job n'est pas exécuté
        tout seul. Le test l'invoque donc directement, comme le ferait un
        worker ``queue_job``.
        """
        ticket = self.env['js.depense.ticket'].message_new({
            'subject': "Ticket",
            'email_from': "employe@example.com",
        })
        self._attach(ticket)

        def fake_run_vision_phase(self, ticket_arg, provider=None):
            ticket_arg.write({'state': 'to_validate'})
            # json_from_vision=True : pas d'étape texte séparée à enfiler.
            return SimpleNamespace(json_from_vision=True)

        with patch.object(
                type(self.env['js.ticket.extractor']),
                'run_vision_phase', fake_run_vision_phase):
            self.env['js.depense.ticket']._cron_analyze_pending_tickets()
            ticket.invalidate_recordset()
            self.assertEqual(ticket.state, 'analyzing')
            self.assertFalse(ticket.needs_ai_analysis)

            ticket._job_batch_vision()

        ticket.invalidate_recordset()
        self.assertFalse(ticket.needs_ai_analysis)
        self.assertEqual(ticket.state, 'to_validate')
        # Le ticket attend une vérification humaine, il n'est pas comptabilisé.
        self.assertFalse(ticket.move_id)

    def test_cron_stops_after_repeated_failures(self):
        """Trois échecs consécutifs (cron + job) épuisent les tentatives.

        Chaque tour ne relance le job que si le cron précédent l'a
        effectivement enfilé (``state == 'analyzing'``) : une fois les
        trois tentatives épuisées, le cron n'enfile plus rien et le job
        n'est pas rejoué, exactement comme dans le déploiement réel.
        """
        ticket = self.env['js.depense.ticket'].message_new({
            'subject': "Ticket",
            'email_from': "employe@example.com",
        })
        self._attach(ticket)

        def failing_run_vision_phase(self, ticket_arg, provider=None):
            raise ValueError("moteur indisponible")

        with patch.object(
                type(self.env['js.ticket.extractor']),
                'run_vision_phase', failing_run_vision_phase):
            for _attempt in range(4):
                self.env['js.depense.ticket']._cron_analyze_pending_tickets()
                ticket.invalidate_recordset()
                if ticket.state == 'analyzing':
                    ticket._job_batch_vision()
                    ticket.invalidate_recordset()

        self.assertFalse(ticket.needs_ai_analysis)
        self.assertEqual(ticket.ai_attempt_count, 3)
        self.assertIn("moteur indisponible", ticket.ai_error_message or '')

    def test_email_without_usable_attachment_is_abandoned(self):
        ticket = self.env['js.depense.ticket'].message_new({
            'subject': "Bonjour",
            'email_from': "employe@example.com",
        })
        self.env['js.depense.ticket']._cron_analyze_pending_tickets()
        ticket.invalidate_recordset()
        self.assertFalse(ticket.needs_ai_analysis)
        self.assertIn("exploitable", ticket.ai_error_message or '')

    def test_retry_resets_counters(self):
        ticket = self.env['js.depense.ticket'].message_new({
            'subject': "Ticket",
            'email_from': "employe@example.com",
        })
        # Un ticket resté bloqué à « Analyse IA » (job jamais abouti) doit
        # pouvoir être repris : la relance manuelle le repasse en brouillon.
        ticket.write({
            'state': 'analyzing',
            'ai_attempt_count': 3,
            'ai_error_message': "erreur",
        })
        ticket.action_retry_analysis()
        self.assertEqual(ticket.state, 'draft')
        self.assertTrue(ticket.needs_ai_analysis)
        self.assertEqual(ticket.ai_attempt_count, 0)
        self.assertFalse(ticket.ai_error_message)

    # ------------------------------------------------------------------
    # Dépôt groupé
    # ------------------------------------------------------------------
    def test_upload_creates_one_ticket_per_photo(self):
        attachments = self.env['ir.attachment'].create([
            {'name': "ticket1.png", 'mimetype': 'image/png',
             'datas': base64.b64encode(_fake_png())},
            {'name': "ticket2.png", 'mimetype': 'image/png',
             'datas': base64.b64encode(_fake_png())},
        ])
        wizard = self.env['js.depense.ticket.upload'].create({
            'attachment_ids': [(6, 0, attachments.ids)],
            'journal_id': self.journal.id,
        })
        action = wizard.action_create_tickets()

        tickets = self.env['js.depense.ticket'].search(action['domain'])
        self.assertEqual(len(tickets), 2)
        for ticket in tickets:
            self.assertEqual(ticket.origin, 'upload')
            # L'analyse est toujours enfilée immédiatement (voir
            # docs/05_IA.md) : needs_ai_analysis retombe à faux dès
            # l'enfilement, le job lui-même n'étant pas exécuté ici.
            self.assertFalse(ticket.needs_ai_analysis)
            self.assertEqual(ticket.state, 'analyzing')
            self.assertEqual(len(ticket.attachment_ids), 1)

    def test_upload_failure_does_not_block_other_tickets(self):
        """L'échec d'un ticket n'empêche pas le traitement des autres.

        Le dépôt enfile toujours un job (voir docs/05_IA.md) plutôt que
        d'appeler le moteur en direct : le test exécute ce job
        manuellement, comme le ferait un worker ``queue_job``, pour
        vérifier que l'échec de chaque ticket reste isolé au sein du lot.
        """
        attachments = self.env['ir.attachment'].create([
            {'name': "ticket1.png", 'mimetype': 'image/png',
             'datas': base64.b64encode(_fake_png())},
            {'name': "ticket2.png", 'mimetype': 'image/png',
             'datas': base64.b64encode(_fake_png())},
        ])
        wizard = self.env['js.depense.ticket.upload'].create({
            'attachment_ids': [(6, 0, attachments.ids)],
            'journal_id': self.journal.id,
        })

        def failing_run_vision_phase(self, ticket_arg, provider=None):
            raise ValueError("moteur indisponible")

        with patch.object(
                type(self.env['js.ticket.extractor']),
                'run_vision_phase', failing_run_vision_phase):
            action = wizard.action_create_tickets()
            tickets = self.env['js.depense.ticket'].search(action['domain'])
            self.assertEqual(len(tickets), 2)
            for ticket in tickets:
                self.assertEqual(ticket.state, 'analyzing')
            tickets._job_batch_vision()

        tickets.invalidate_recordset()
        for ticket in tickets:
            self.assertIn("moteur indisponible", ticket.ai_error_message or '')

    def test_upload_passes_chosen_ai_provider_to_ticket(self):
        provider = self.env['js.ai.provider'].create({
            'name': "Moteur choisi", 'provider_type': 'ollama',
        })
        attachment = self.env['ir.attachment'].create({
            'name': "ticket.png", 'mimetype': 'image/png',
            'datas': base64.b64encode(_fake_png()),
        })
        wizard = self.env['js.depense.ticket.upload'].create({
            'attachment_ids': [(6, 0, attachment.ids)],
            'journal_id': self.journal.id,
            'ai_provider_id': provider.id,
        })
        action = wizard.action_create_tickets()

        ticket = self.env['js.depense.ticket'].browse(action['res_id'])
        self.assertEqual(ticket.ai_provider_id, provider)

    def test_upload_ai_provider_count_reflects_configured_providers(self):
        count_before = self.env['js.ai.provider'].search_count([])
        wizard = self.env['js.depense.ticket.upload'].create({
            'journal_id': self.journal.id,
        })
        self.assertEqual(wizard.ai_provider_count, count_before)

        self.env['js.ai.provider'].create({
            'name': "Second moteur", 'provider_type': 'ollama',
        })
        other_wizard = self.env['js.depense.ticket.upload'].create({
            'journal_id': self.journal.id,
        })
        self.assertEqual(other_wizard.ai_provider_count, count_before + 1)
