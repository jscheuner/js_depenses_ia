# -*- coding: utf-8 -*-
"""Contrôles de la réception des tickets : courriel entrant, dépôt groupé
et analyse différée.

L'exigence de fond est qu'aucun ticket reçu ne se comptabilise tout seul :
l'analyse pré-remplit, un humain vérifie et valide.
"""

import base64

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
        ticket = self.env['js.depense.ticket'].message_new({
            'subject': "Ticket",
            'email_from': "employe@example.com",
        })
        self._attach(ticket)

        def fake_analyze(self, ticket_arg, provider=None):
            ticket_arg.write({'state': 'to_validate'})
            return {}

        with patch.object(
                type(self.env['js.ticket.extractor']), 'analyze', fake_analyze):
            self.env['js.depense.ticket']._cron_analyze_pending_tickets()

        ticket.invalidate_recordset()
        self.assertFalse(ticket.needs_ai_analysis)
        self.assertEqual(ticket.state, 'to_validate')
        # Le ticket attend une vérification humaine, il n'est pas comptabilisé.
        self.assertFalse(ticket.move_id)

    def test_cron_stops_after_repeated_failures(self):
        ticket = self.env['js.depense.ticket'].message_new({
            'subject': "Ticket",
            'email_from': "employe@example.com",
        })
        self._attach(ticket)

        def failing_analyze(self, ticket_arg, provider=None):
            raise ValueError("moteur indisponible")

        with patch.object(
                type(self.env['js.ticket.extractor']),
                'analyze', failing_analyze):
            for _attempt in range(4):
                self.env['js.depense.ticket']._cron_analyze_pending_tickets()

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
        ticket.write({'ai_attempt_count': 3, 'ai_error_message': "erreur"})
        ticket.action_retry_analysis()
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
            'analyze_now': False,
        })
        action = wizard.action_create_tickets()

        tickets = self.env['js.depense.ticket'].search(action['domain'])
        self.assertEqual(len(tickets), 2)
        for ticket in tickets:
            self.assertEqual(ticket.origin, 'upload')
            self.assertTrue(ticket.needs_ai_analysis)
            self.assertEqual(len(ticket.attachment_ids), 1)

    def test_upload_failure_does_not_block_other_tickets(self):
        attachments = self.env['ir.attachment'].create([
            {'name': "ticket1.png", 'mimetype': 'image/png',
             'datas': base64.b64encode(_fake_png())},
            {'name': "ticket2.png", 'mimetype': 'image/png',
             'datas': base64.b64encode(_fake_png())},
        ])
        wizard = self.env['js.depense.ticket.upload'].create({
            'attachment_ids': [(6, 0, attachments.ids)],
            'journal_id': self.journal.id,
            'analyze_now': True,
        })

        def failing_analyze(self, ticket_arg, provider=None):
            raise ValueError("moteur indisponible")

        with patch.object(
                type(self.env['js.ticket.extractor']),
                'analyze', failing_analyze):
            action = wizard.action_create_tickets()

        tickets = self.env['js.depense.ticket'].search(action['domain'])
        self.assertEqual(len(tickets), 2)
        for ticket in tickets:
            self.assertIn("moteur indisponible", ticket.ai_error_message or '')
