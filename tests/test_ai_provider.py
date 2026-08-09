# -*- coding: utf-8 -*-
"""Contrôles du moteur IA : configuration et protection contre l'erreur de
saisie la plus fréquente, qui consiste à coller la clé d'API dans le champ
prévu pour le *nom* du paramètre système qui la contient.
"""

from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestAiProvider(TransactionCase):

    def test_url_is_automatic_for_anthropic(self):
        """Choisir Anthropic suffit : l'adresse et le rangement de la clé
        sont déterminés sans rien demander à l'utilisateur."""
        provider = self.env['js.ai.provider'].create({
            'name': "Anthropic",
            'provider_type': 'anthropic',
        })
        self.assertEqual(provider.base_url, 'https://api.anthropic.com/v1')
        self.assertTrue(provider.url_is_fixed)
        self.assertEqual(
            provider.api_key_param, 'js_depenses_ia.anthropic_api_key')

    def test_url_follows_provider_type_change(self):
        """Changer de service réaligne l'adresse automatiquement."""
        provider = self.env['js.ai.provider'].create({
            'name': "Test",
            'provider_type': 'anthropic',
        })
        provider.provider_type = 'openai'
        self.assertEqual(provider.base_url, 'https://api.openai.com/v1')
        self.assertEqual(
            provider.api_key_param, 'js_depenses_ia.openai_api_key')

    def test_fixed_url_cannot_be_silently_wrong(self):
        """Une adresse erronée saisie pour un service imposé est corrigée."""
        provider = self.env['js.ai.provider'].create({
            'name': "Anthropic",
            'provider_type': 'anthropic',
            'base_url': 'https://mauvaise-adresse.example.com',
        })
        # L'adresse officielle est rétablie d'office, même fournie à la création.
        self.assertEqual(provider.base_url, 'https://api.anthropic.com/v1')

        provider.base_url = 'https://encore-une-mauvaise.example.com'
        self.assertEqual(provider.base_url, 'https://api.anthropic.com/v1')

    def test_ollama_url_is_customisable(self):
        """Pour un serveur local, l'adresse reste librement modifiable."""
        provider = self.env['js.ai.provider'].create({
            'name': "Ollama distant",
            'provider_type': 'ollama',
            'base_url': 'http://192.168.1.68:11434',
        })
        self.assertFalse(provider.url_is_fixed)
        self.assertEqual(provider.base_url, 'http://192.168.1.68:11434')
        # Un serveur local n'exige aucune clé.
        self.assertFalse(provider.api_key_param)

    def test_pasting_api_key_in_param_name_is_rejected(self):
        """Une clé collée dans le champ « nom du paramètre » est détectée."""
        with self.assertRaises(ValidationError):
            self.env['js.ai.provider'].create({
                'name': "Anthropic",
                'provider_type': 'anthropic',
                'base_url': 'https://api.anthropic.com/v1',
                'api_key_param':
                    'sk-ant-api03-CiM4oL6UhAl1SbzpuhwYQKvn2rr86BQD1xzSbtE',
            })

    def test_valid_param_name_is_accepted(self):
        provider = self.env['js.ai.provider'].create({
            'name': "Anthropic",
            'provider_type': 'anthropic',
            'base_url': 'https://api.anthropic.com/v1',
            'api_key_param': 'js_depenses_ia.anthropic_api_key',
        })
        self.assertTrue(provider)

    def test_api_key_value_is_stored_as_system_parameter(self):
        """Coller une clé dans le champ dédié l'enregistre au bon endroit,
        jamais sur l'enregistrement lui-même."""
        provider = self.env['js.ai.provider'].create({
            'name': "Anthropic",
            'provider_type': 'anthropic',
            'base_url': 'https://api.anthropic.com/v1',
        })
        provider.api_key_value = 'sk-ant-secret-value'

        self.assertEqual(
            provider.api_key_param, 'js_depenses_ia.anthropic_api_key')
        stored = self.env['ir.config_parameter'].sudo().get_param(
            'js_depenses_ia.anthropic_api_key')
        self.assertEqual(stored, 'sk-ant-secret-value')

        # La clé n'est jamais réaffichée, y compris juste après l'avoir saisie.
        provider.invalidate_recordset()
        self.assertFalse(provider.api_key_value)
        self.assertTrue(provider.api_key_configured)

    def test_no_key_configured_by_default(self):
        provider = self.env['js.ai.provider'].create({
            'name': "Anthropic",
            'provider_type': 'anthropic',
            'api_key_param': 'js_depenses_ia.unused_test_key',
        })
        self.assertFalse(provider.api_key_configured)

    def test_anthropic_retries_without_temperature_when_deprecated(self):
        """Reproduit l'erreur réelle observée avec Claude Sonnet 5 :
        ``temperature`` y est rejeté, la requête doit être rejouée sans ce
        paramètre plutôt que d'échouer."""
        provider = self.env['js.ai.provider'].create({
            'name': "Anthropic",
            'provider_type': 'anthropic',
            'model_vision': 'claude-sonnet-5',
        })

        calls = []

        def fake_post(self, url, payload):
            calls.append(payload)
            if 'temperature' in payload:
                raise UserError(
                    "Le moteur « Anthropic » n'a pas répondu correctement.\n\n"
                    "HTTP 400 : {\"type\":\"error\",\"error\":{\"type\":"
                    "\"invalid_request_error\",\"message\":\"`temperature` "
                    "is deprecated for this model.\"}}")
            return {'content': [{'type': 'text', 'text': 'ok'}]}

        with patch.object(type(provider), '_post', fake_post):
            content = provider._ask_anthropic("Bonjour", None, [])

        self.assertEqual(content, 'ok')
        self.assertEqual(len(calls), 2)
        self.assertIn('temperature', calls[0])
        self.assertNotIn('temperature', calls[1])

    def test_anthropic_forces_schema_via_tool_choice(self):
        """Un schéma fourni doit forcer un appel d'outil, faute de quoi le
        modèle reste libre de choisir sa propre structure de réponse (c'est
        ce qui produisait des clés en français au lieu du schéma attendu)."""
        provider = self.env['js.ai.provider'].create({
            'name': "Anthropic",
            'provider_type': 'anthropic',
            'supports_json_schema': True,
        })
        schema = {'type': 'object', 'properties': {'total': {'type': 'string'}}}

        calls = []

        def fake_post(self, url, payload):
            calls.append(payload)
            return {'content': [{'type': 'tool_use', 'name': 'ticket_data',
                                  'input': {'total': '12.30'}}]}

        with patch.object(type(provider), '_post', fake_post):
            content = provider._ask_anthropic("Bonjour", None, [], schema)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['tool_choice'],
                          {'type': 'tool', 'name': 'ticket_data'})
        self.assertEqual(calls[0]['tools'][0]['input_schema'], schema)
        self.assertEqual(content, '{"total": "12.30"}')

    def test_anthropic_ignores_schema_when_not_supported(self):
        """Un moteur marqué comme ne gérant pas la sortie structurée ne doit
        pas se voir imposer d'outil, et continue de répondre en texte."""
        provider = self.env['js.ai.provider'].create({
            'name': "Anthropic",
            'provider_type': 'anthropic',
            'supports_json_schema': False,
        })
        schema = {'type': 'object', 'properties': {'total': {'type': 'string'}}}

        calls = []

        def fake_post(self, url, payload):
            calls.append(payload)
            return {'content': [{'type': 'text', 'text': 'ok'}]}

        with patch.object(type(provider), '_post', fake_post):
            content = provider._ask_anthropic("Bonjour", None, [], schema)
        self.assertEqual(content, 'ok')
        self.assertNotIn('tools', calls[0])

    def test_anthropic_other_errors_are_not_swallowed(self):
        """Une erreur sans rapport avec la température doit remonter."""
        provider = self.env['js.ai.provider'].create({
            'name': "Anthropic",
            'provider_type': 'anthropic',
        })

        def fake_post(self, url, payload):
            raise UserError(
                "Le moteur « Anthropic » n'a pas répondu correctement.\n\n"
                "HTTP 401 : clé d'API invalide.")

        with patch.object(type(provider), '_post', fake_post):
            with self.assertRaises(UserError):
                provider._ask_anthropic("Bonjour", None, [])
