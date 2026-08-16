# -*- coding: utf-8 -*-

import base64
import json
import logging
import re
import time

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Un nom de paramètre système ressemble à « module.mon_parametre » : des
# segments alphanumériques séparés par des points. Une vraie clé d'API ne
# respecte jamais ce format (préfixes « sk-», longues chaînes aléatoires
# sans point, etc.), ce qui permet de détecter à coup sûr qu'elle a été
# collée au mauvais endroit.
_PARAM_NAME_RE = re.compile(r'^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$')

# Services dont l'adresse est imposée par l'éditeur : il n'y a rien à
# choisir, l'URL est donc déterminée automatiquement et le champ reste en
# lecture seule.
_FIXED_URLS = {
    'openai': 'https://api.openai.com/v1',
    'anthropic': 'https://api.anthropic.com/v1',
    'mistral': 'https://api.mistral.ai/v1',
}

# Services dont l'adresse dépend de l'installation : seule une valeur de
# départ est proposée, l'utilisateur devant l'adapter à son infrastructure.
_SUGGESTED_URLS = {
    'ollama': 'http://localhost:11434',
}

# Nom de paramètre système utilisé pour ranger la clé d'API. Ce détail
# d'implémentation est déterminé automatiquement et masqué à l'utilisateur.
_DEFAULT_KEY_PARAMS = {
    'openai': 'js_depenses_ia.openai_api_key',
    'anthropic': 'js_depenses_ia.anthropic_api_key',
    'mistral': 'js_depenses_ia.mistral_api_key',
}

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None
    _logger.warning(
        "La bibliothèque « requests » est absente : l'analyse IA sera "
        "indisponible.")

PROVIDER_TYPES = [
    ('ollama', "Ollama (local ou distant)"),
    ('openai', "OpenAI"),
    ('anthropic', "Anthropic (Claude)"),
    ('mistral', "Mistral AI"),
    ('openai_compat', "API compatible OpenAI"),
]


class JsAiProvider(models.Model):
    """Moteur d'analyse configurable.

    Le module ne dépend d'aucun fournisseur en particulier : un moteur
    local (Ollama) et un moteur distant peuvent coexister, ce qui permet
    de comparer leur fiabilité sur des tickets réels avant d'arbitrer.
    """

    _name = 'js.ai.provider'
    _description = "Moteur d'analyse IA"
    _order = 'sequence, id'

    name = fields.Char(string="Nom", required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    is_default = fields.Boolean(
        string="Par défaut",
        help="Moteur retenu lorsqu'aucun n'est précisé sur le ticket.")

    provider_type = fields.Selection(
        PROVIDER_TYPES, string="Type", required=True, default='ollama')
    base_url = fields.Char(
        string="URL du service", required=True,
        compute='_compute_base_url', store=True, readonly=False,
        help="Déterminée automatiquement pour les services dont l'adresse "
             "est imposée par l'éditeur. Modifiable uniquement pour un "
             "serveur local ou une passerelle interne.")
    url_is_fixed = fields.Boolean(
        string="Adresse imposée", compute='_compute_url_is_fixed')
    api_key_param = fields.Char(
        string="Nom du paramètre système",
        compute='_compute_api_key_param', store=True, readonly=False,
        help="Détail technique : nom sous lequel la clé est rangée dans les "
             "paramètres système. Déterminé automatiquement, il n'y a "
             "normalement rien à y modifier.")
    api_key_value = fields.Char(
        string="Clé d'API",
        compute='_compute_api_key_value', inverse='_inverse_api_key_value',
        help="Collez ici la clé fournie par le service. Elle est aussitôt "
             "enregistrée dans les paramètres système et n'est jamais "
             "conservée dans cette fiche : ce champ reste vide à chaque "
             "réouverture, même lorsqu'une clé est déjà configurée.")
    api_key_configured = fields.Boolean(
        string="Clé configurée", compute='_compute_api_key_configured')

    model_vision = fields.Char(
        string="Modèle vision", default='llava',
        help="Modèle chargé de lire l'image du ticket.")
    model_text = fields.Char(
        string="Modèle texte",
        help="Modèle utilisé pour l'affectation des comptes. À défaut, le "
             "modèle vision est réutilisé.")

    timeout = fields.Integer(string="Délai (s)", default=180)
    temperature = fields.Float(
        string="Température", default=0.0, digits=(3, 2),
        help="Zéro donne les résultats les plus reproductibles, ce qui est "
             "recherché pour de l'extraction de données.")
    max_retries = fields.Integer(
        string="Tentatives", default=2,
        help="Nombre de relances en cas d'échec réseau.")
    supports_json_schema = fields.Boolean(
        string="Sortie structurée", default=True,
        help="Le moteur sait contraindre sa réponse à un schéma JSON.")
    json_from_vision = fields.Boolean(
        string="JSON directement par le modèle vision", default=False,
        help="Si activé, le modèle vision produit directement le JSON "
             "structuré du ticket (schéma imposé) et l'étape de "
             "structuration séparée par le modèle texte est sautée. À "
             "réserver aux moteurs dont le modèle vision suit fiablement "
             "un schéma strict même en présence d'une image — c'est "
             "souvent le cas des moteurs cloud, rarement celui des "
             "modèles vision locaux (qwen3-vl, llava...), qui lisent bien "
             "l'image mais structurent mal le JSON dans le même appel. "
             "Décoché par défaut.")

    log_ids = fields.One2many('js.ai.log', 'provider_id', string="Journal")
    log_count = fields.Integer(compute='_compute_log_count')

    @api.depends('log_ids')
    def _compute_log_count(self):
        for provider in self:
            provider.log_count = len(provider.log_ids)

    def _compute_api_key_value(self):
        # La clé n'est jamais réaffichée en clair : ce champ sert uniquement
        # à la saisir, jamais à la consulter.
        for provider in self:
            provider.api_key_value = False

    def _compute_api_key_configured(self):
        icp = self.env['ir.config_parameter'].sudo()
        for provider in self:
            provider.api_key_configured = bool(
                provider.api_key_param and icp.get_param(provider.api_key_param))

    def _inverse_api_key_value(self):
        """Enregistre la clé saisie dans les paramètres système.

        La clé n'est jamais écrite sur cet enregistrement : elle transite
        directement vers ``ir.config_parameter``, à l'endroit même que
        pointe ``api_key_param``. C'est ce mécanisme qui évite la confusion
        entre « le nom du paramètre » et « la clé elle-même ».
        """
        icp = self.env['ir.config_parameter'].sudo()
        for provider in self:
            if not provider.api_key_value:
                continue
            if not provider.api_key_param:
                provider.api_key_param = provider._default_api_key_param_name()
            icp.set_param(provider.api_key_param, provider.api_key_value.strip())

    def _default_api_key_param_name(self):
        self.ensure_one()
        default = _DEFAULT_KEY_PARAMS.get(self.provider_type)
        if default:
            return default
        return 'js_depenses_ia.api_key_provider_%s' % (self.id or 'new')

    @api.depends('provider_type')
    def _compute_url_is_fixed(self):
        for provider in self:
            provider.url_is_fixed = provider.provider_type in _FIXED_URLS

    @api.depends('provider_type')
    def _compute_base_url(self):
        """Détermine l'adresse du service.

        Pour Anthropic, OpenAI ou Mistral, l'adresse est imposée par
        l'éditeur : elle est appliquée d'office, sans rien demander à
        l'utilisateur. Pour un serveur local comme Ollama, seule une valeur
        de départ est proposée, et toute personnalisation est conservée.
        """
        for provider in self:
            fixed = _FIXED_URLS.get(provider.provider_type)
            if fixed:
                provider.base_url = fixed
            elif not provider.base_url:
                provider.base_url = _SUGGESTED_URLS.get(
                    provider.provider_type, '')

    @api.depends('provider_type')
    def _compute_api_key_param(self):
        """Détermine où ranger la clé, sans intervention de l'utilisateur."""
        for provider in self:
            if provider.provider_type == 'ollama':
                # Un serveur local n'exige aucune authentification.
                provider.api_key_param = False
                continue
            default = _DEFAULT_KEY_PARAMS.get(provider.provider_type)
            if default:
                provider.api_key_param = default
            elif not provider.api_key_param:
                provider.api_key_param = (
                    'js_depenses_ia.api_key_provider_%s' % (provider.id or 'new'))

    @api.constrains('api_key_param')
    def _check_api_key_param_is_a_name(self):
        """Empêche qu'une clé d'API soit collée dans le mauvais champ.

        Un nom de paramètre système ressemble toujours à
        « module.nom_du_parametre ». Toute valeur qui ne respecte pas ce
        format est presque certainement une clé d'API saisie par erreur.
        """
        for provider in self:
            value = (provider.api_key_param or '').strip()
            if not value or _PARAM_NAME_RE.match(value):
                continue
            preview = value[:8] + '…' if len(value) > 8 else value
            raise ValidationError(_(
                "Le champ « Nom du paramètre système » attend un nom "
                "technique, comme « js_depenses_ia.anthropic_api_key », "
                "et non la clé d'API elle-même.\n\n"
                "La valeur saisie (« %(preview)s ») ressemble à une clé "
                "plutôt qu'à un nom de paramètre.\n\n"
                "Utilisez plutôt le champ « Clé d'API » situé juste "
                "au-dessus : collez-y votre clé, elle sera enregistrée "
                "automatiquement au bon endroit.",
                preview=preview))

    @api.model_create_multi
    def create(self, vals_list):
        providers = super().create(vals_list)
        providers._ensure_single_default()
        providers._enforce_fixed_url()
        return providers

    def write(self, vals):
        result = super().write(vals)
        if vals.get('is_default'):
            self._ensure_single_default()
        if 'base_url' in vals or 'provider_type' in vals:
            self._enforce_fixed_url()
        return result

    def _enforce_fixed_url(self):
        """Garantit l'adresse officielle des services à URL imposée.

        Le champ calculé ne suffit pas : une valeur fournie explicitement à
        la création prendrait le pas sur le calcul. Or une adresse erronée
        sur un service comme Anthropic ne produirait qu'un échec réseau
        obscur, très difficile à diagnostiquer.
        """
        for provider in self:
            fixed = _FIXED_URLS.get(provider.provider_type)
            if fixed and provider.base_url != fixed:
                super(JsAiProvider, provider).write({'base_url': fixed})

    def _ensure_single_default(self):
        """Un seul moteur peut porter le drapeau « par défaut »."""
        flagged = self.filtered('is_default')
        if not flagged:
            return
        keeper = flagged[-1]
        others = self.search([
            ('is_default', '=', True),
            ('id', '!=', keeper.id),
        ])
        if others:
            super(JsAiProvider, others).write({'is_default': False})

    @api.model
    def _get_default(self):
        provider = self.search([('is_default', '=', True)], limit=1)
        return provider or self.search([], limit=1)

    # ------------------------------------------------------------------
    # Accès réseau
    # ------------------------------------------------------------------
    def _get_api_key(self):
        self.ensure_one()
        if not self.api_key_param:
            return ''
        return self.env['ir.config_parameter'].sudo().get_param(
            self.api_key_param, '')

    def _headers(self):
        self.ensure_one()
        headers = {'Content-Type': 'application/json'}
        key = self._get_api_key()
        if not key:
            return headers

        if self.provider_type == 'anthropic':
            headers['x-api-key'] = key
            headers['anthropic-version'] = '2023-06-01'
        else:
            headers['Authorization'] = 'Bearer %s' % key
        return headers

    def _check_available(self):
        self.ensure_one()
        if requests is None:
            raise UserError(_(
                "La bibliothèque Python « requests » est absente du serveur "
                "Odoo. Installez-la pour activer l'analyse IA."))
        if not self.base_url:
            raise UserError(_(
                "Le moteur « %s » n'a pas d'URL de service.", self.name))

    def _post(self, url, payload):
        """Appel HTTP avec relances, retournant le corps décodé."""
        self.ensure_one()
        self._check_available()

        last_error = None
        for attempt in range(max(1, self.max_retries + 1)):
            try:
                response = requests.post(
                    url,
                    headers=self._headers(),
                    data=json.dumps(payload),
                    timeout=self.timeout or 180,
                )
                if response.status_code >= 400:
                    last_error = "HTTP %s : %s" % (
                        response.status_code, response.text[:500])
                    _logger.warning("Appel IA en échec (%s)", last_error)
                    if response.status_code < 500:
                        break
                else:
                    return response.json()
            except Exception as error:  # réseau, délai, JSON invalide
                last_error = str(error)
                _logger.warning(
                    "Appel IA interrompu (tentative %s) : %s",
                    attempt + 1, error)
            time.sleep(1.5 * (attempt + 1))

        raise UserError(_(
            "Le moteur « %(name)s » n'a pas répondu correctement.\n\n%(error)s",
            name=self.name, error=last_error or _("cause inconnue")))

    # ------------------------------------------------------------------
    # Requêtes
    # ------------------------------------------------------------------
    def ask(self, prompt, system=None, images=None, json_schema=None):
        """Interroge le moteur et retourne le texte de la réponse.

        :param images: liste de contenus binaires d'images (``bytes``)
        :param json_schema: schéma imposé à la réponse, si le moteur le gère
        """
        self.ensure_one()
        images = images or []
        started = time.time()

        if self.provider_type == 'ollama':
            content = self._ask_ollama(prompt, system, images, json_schema)
        elif self.provider_type == 'anthropic':
            content = self._ask_anthropic(prompt, system, images, json_schema)
        else:
            content = self._ask_openai_like(prompt, system, images, json_schema)

        duration = time.time() - started
        return content, duration

    def _encode(self, image_bytes):
        if isinstance(image_bytes, str):
            return image_bytes
        return base64.b64encode(image_bytes).decode('ascii')

    def _ask_ollama(self, prompt, system, images, json_schema):
        self.ensure_one()
        model = (self.model_vision if images else
                 (self.model_text or self.model_vision))
        message = {'role': 'user', 'content': prompt}
        if images:
            message['images'] = [self._encode(img) for img in images]

        messages = []
        if system:
            messages.append({'role': 'system', 'content': system})
        messages.append(message)

        payload = {
            'model': model,
            'messages': messages,
            'stream': False,
            'options': {'temperature': self.temperature or 0.0},
        }
        if json_schema and self.supports_json_schema:
            payload['format'] = json_schema

        url = '%s/api/chat' % self.base_url.rstrip('/')
        data = self._post(url, payload)
        return (data.get('message') or {}).get('content', '')

    def _ask_openai_like(self, prompt, system, images, json_schema):
        self.ensure_one()
        model = (self.model_vision if images else
                 (self.model_text or self.model_vision))

        content = [{'type': 'text', 'text': prompt}]
        for image in images:
            content.append({
                'type': 'image_url',
                'image_url': {
                    'url': 'data:image/jpeg;base64,%s' % self._encode(image),
                },
            })

        messages = []
        if system:
            messages.append({'role': 'system', 'content': system})
        messages.append({'role': 'user', 'content': content})

        payload = {
            'model': model,
            'messages': messages,
            'temperature': self.temperature or 0.0,
        }
        if json_schema and self.supports_json_schema:
            payload['response_format'] = {
                'type': 'json_schema',
                'json_schema': {
                    'name': 'ticket',
                    'schema': json_schema,
                    'strict': True,
                },
            }

        url = '%s/chat/completions' % self.base_url.rstrip('/')
        data = self._post(url, payload)
        choices = data.get('choices') or []
        if not choices:
            return ''
        return (choices[0].get('message') or {}).get('content', '')

    def _ask_anthropic(self, prompt, system, images, json_schema=None):
        """Interroge l'API Messages d'Anthropic.

        Les modèles récents (Claude Sonnet 5, Opus 5, Fable 5...) recourent
        à un raisonnement adaptatif permanent et **rejettent** le paramètre
        ``temperature`` avec une erreur HTTP 400. Les modèles plus anciens
        (Haiku 4.5, par exemple) l'acceptent encore. Plutôt que de maintenir
        une liste de modèles à jour, la requête est tentée avec
        ``temperature``, puis rejouée sans ce paramètre si l'API signale
        qu'il est obsolète pour le modèle interrogé.

        L'API Messages n'a pas de paramètre équivalent au ``response_format``
        d'OpenAI : la seule façon fiable d'imposer un schéma est de le
        déclarer comme un outil unique et de forcer son appel
        (``tool_choice``). Sans cela, le paramètre ``json_schema`` était
        jusqu'ici silencieusement ignoré et le modèle répondait avec la
        structure de son choix.
        """
        self.ensure_one()
        model = (self.model_vision if images else
                 (self.model_text or self.model_vision))

        content = []
        for image in images:
            content.append({
                'type': 'image',
                'source': {
                    'type': 'base64',
                    'media_type': 'image/jpeg',
                    'data': self._encode(image),
                },
            })
        content.append({'type': 'text', 'text': prompt})

        payload = {
            'model': model,
            'max_tokens': 4096,
            'messages': [{'role': 'user', 'content': content}],
        }
        if system:
            payload['system'] = system

        use_tool = bool(json_schema and self.supports_json_schema)
        if use_tool:
            payload['tools'] = [{
                'name': 'ticket_data',
                'description': "Donnée structurée extraite du ticket.",
                'input_schema': json_schema,
            }]
            payload['tool_choice'] = {'type': 'tool', 'name': 'ticket_data'}

        url = '%s/messages' % self.base_url.rstrip('/')

        try:
            data = self._post(
                url, dict(payload, temperature=self.temperature or 0.0))
        except UserError as error:
            if self._is_temperature_unsupported_error(error):
                _logger.info(
                    "Le modèle « %s » n'accepte plus le paramètre "
                    "température : nouvel essai sans ce réglage.", model)
                data = self._post(url, payload)
            else:
                raise

        blocks = data.get('content') or []
        if use_tool:
            for block in blocks:
                if isinstance(block, dict) and block.get('type') == 'tool_use':
                    return json.dumps(block.get('input') or {})
        return ''.join(b.get('text', '') for b in blocks if isinstance(b, dict))

    @api.model
    def _is_temperature_unsupported_error(self, error):
        message = str(error).lower()
        return 'temperature' in message and (
            'deprecat' in message or 'not supported' in message
            or 'unsupported' in message)

    # ------------------------------------------------------------------
    # Diagnostic
    # ------------------------------------------------------------------
    def action_test_connection(self):
        """Vérifie que le moteur répond, sans consommer d'image."""
        self.ensure_one()
        try:
            content, duration = self.ask(
                prompt="Réponds exactement : OK",
                system="Tu es un service de test. Réponds sobrement.",
            )
            message = _(
                "Réponse obtenue en %(duration).1f s :\n%(content)s",
                duration=duration, content=(content or '')[:200])
            kind = 'success'
        except Exception as error:
            message = str(error)
            kind = 'danger'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': self.name,
                'message': message,
                'type': kind,
                'sticky': kind == 'danger',
            },
        }

    def action_view_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Journal des échanges"),
            'res_model': 'js.ai.log',
            'view_mode': 'list,form',
            'domain': [('provider_id', '=', self.id)],
        }
