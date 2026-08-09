# -*- coding: utf-8 -*-
"""Chaîne d'extraction d'un ticket de dépense.

Le modèle de langage ne décide jamais seul. Sa proposition traverse
plusieurs filtres déterministes :

    image -> OCR -> modèle -> analyse Decimal -> contrôle arithmétique
          -> relance ciblée si le compte n'y est pas
          -> affectation comptable (règles apprises d'abord)

Toute valeur qui ne satisfait pas le contrôle arithmétique est signalée à
l'utilisateur plutôt que d'être écrite en silence.
"""

import json
import logging
import re
from decimal import Decimal

from odoo import api, models, _
from odoo.exceptions import UserError

from ..utils.amount_parser import parse_amount, quantize, AmountParseError
from ..utils.text_normalizer import normalize_label
from . import ocr

_logger = logging.getLogger(__name__)

# Schéma imposé à la réponse. Les montants sont demandés sous forme de
# chaînes : un modèle produit trop souvent des nombres mal formés, alors
# qu'une chaîne peut être analysée rigoureusement côté Python.
#
# `additionalProperties: False` et un `required` couvrant systématiquement
# toutes les clés (y compris dans les objets imbriqués) ne sont pas de la
# ceinture-bretelles : c'est ce qui permet d'activer le mode strict des
# moteurs qui le proposent (OpenAI et compatibles, Anthropic via un outil
# forcé). Sans ce mode strict, un moteur peut renvoyer une structure de son
# choix — par exemple des clés en français (`articles`, `commerce`...) — et
# le schéma est alors une simple indication ignorée en pratique. Une valeur
# illisible reste une chaîne vide, jamais une clé absente.
TICKET_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'partner_name': {'type': 'string'},
        'summary': {'type': 'string'},
        'date': {'type': 'string'},
        'reference': {'type': 'string'},
        'currency': {'type': 'string'},
        'total': {'type': 'string'},
        'total_vat': {'type': 'string'},
        'lines': {
            'type': 'array',
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'description': {'type': 'string'},
                    'quantity': {'type': 'string'},
                    'unit_price': {'type': 'string'},
                    'amount': {'type': 'string'},
                    'vat_rate': {'type': 'string'},
                },
                'required': ['description', 'quantity', 'unit_price',
                             'amount', 'vat_rate'],
            },
        },
        'vat_summary': {
            'type': 'array',
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'rate': {'type': 'string'},
                    'base': {'type': 'string'},
                    'amount': {'type': 'string'},
                },
                'required': ['rate', 'base', 'amount'],
            },
        },
    },
    'required': ['partner_name', 'summary', 'date', 'reference', 'currency',
                 'total', 'total_vat', 'lines', 'vat_summary'],
}

SYSTEM_PROMPT = """Tu es un assistant spécialisé dans la lecture de tickets \
de caisse et de justificatifs de dépense suisses.

Règles impératives :
- Tu ne transcris que ce qui est réellement imprimé. Tu n'inventes aucun \
montant et tu ne complètes aucune valeur absente.
- Les montants sont recopiés tels quels, avec deux décimales, sans symbole \
monétaire. Exemple : "12.90".
- En Suisse, les montants affichés sur un ticket sont presque toujours TTC.
- Le séparateur de milliers peut être une apostrophe : 1'234.50 vaut 1234.50.
- Une ligne par article. Les remises et consignes sont des lignes à part \
entière, avec un montant négatif si nécessaire.
- Le champ "summary" contient un résumé de la nature de l'achat en 2 à 3 \
mots maximum, en français (par exemple : "essence", "café et pâtisserie", \
"fournitures de bureau"). Ne répète ni l'enseigne ni le montant dedans.
- Si une information est illisible, tu renvoies une chaîne vide (jamais une \
clé absente).
- Tu réponds exclusivement par un objet JSON conforme au schéma, sans \
commentaire ni texte d'accompagnement.
- Le schéma fourni impose déjà les noms de clés, mais si jamais il ne \
t'était pas transmis, utilise impérativement ces clés-ci, en anglais, et \
aucune autre : "partner_name", "summary", "date", "reference", "currency", \
"total", "total_vat", "lines" (liste d'objets avec "description", \
"quantity", "unit_price", "amount", "vat_rate") et "vat_summary" (liste \
d'objets avec "rate", "base", "amount"). Ne traduis jamais ces noms de clés \
en français."""

# Prompt système de l'étape distincte d'affectation des comptes. Ne jamais
# réutiliser SYSTEM_PROMPT ni TICKET_SCHEMA ici : ce sont ceux de la lecture
# du ticket, sans rapport avec la forme de réponse attendue pour cette
# étape (`{"assignments": {...}}`, à clés dynamiques — un schéma JSON strict
# imposerait au contraire une forme fixe et casserait la réponse).
ACCOUNTS_SYSTEM_PROMPT = """Tu es un assistant comptable qui affecte des \
libellés d'articles à un plan comptable suisse.

Règles impératives :
- Tu ne choisis un compte que parmi ceux listés dans le message, en \
recopiant leur code exact.
- Si aucun compte ne convient à un libellé, tu omets ce libellé plutôt que \
de deviner.
- Tu réponds exclusivement par l'objet JSON demandé, sans commentaire ni \
texte d'accompagnement."""


class JsTicketExtractor(models.AbstractModel):
    _name = 'js.ticket.extractor'
    _description = "Chaîne d'extraction des tickets de dépense"

    # ------------------------------------------------------------------
    # Point d'entrée
    # ------------------------------------------------------------------
    @api.model
    def analyze(self, ticket, provider=None):
        """Analyse les pièces d'un ticket et le renseigne.

        :return: dictionnaire de compte rendu
        """
        provider = (provider or ticket.ai_provider_id
                    or ticket.company_id.js_depense_ai_provider_id
                    or self.env['js.ai.provider']._get_default())
        if not provider:
            raise UserError(_(
                "Aucun moteur d'analyse n'est configuré. Créez-en un dans "
                "Dépenses IA > Configuration > Moteurs IA."))

        documents = self._collect_documents(ticket)
        if not documents:
            raise UserError(_(
                "Joignez au moins une photo ou un scan du ticket avant de "
                "lancer l'analyse."))

        use_ocr = ticket.company_id.js_depense_use_ocr
        images, ocr_text = ocr.prepare_documents(documents, use_ocr=use_ocr)
        if not images:
            raise UserError(_(
                "Les pièces jointes n'ont pas pu être converties en images."))

        payload = self._extract_with_retries(
            ticket, provider, images, ocr_text)

        self._apply_to_ticket(ticket, payload, provider)
        return payload

    @api.model
    def _collect_documents(self, ticket):
        documents = []
        for attachment in ticket.attachment_ids:
            try:
                documents.append(attachment.raw)
            except Exception:
                _logger.warning(
                    "Pièce jointe illisible : %s", attachment.display_name)
        return documents

    # ------------------------------------------------------------------
    # Dialogue avec le moteur
    # ------------------------------------------------------------------
    @api.model
    def _extract_with_retries(self, ticket, provider, images, ocr_text):
        """Interroge le moteur, puis le relance tant que le compte n'y est pas.

        La relance n'est pas une simple répétition : elle indique
        précisément l'écart constaté, ce qui conduit le modèle à relire la
        zone fautive plutôt qu'à reproduire son erreur.
        """
        max_iterations = max(1, (ticket.company_id.js_depense_ai_max_iterations or 0) + 1)
        prompt = self._build_extraction_prompt(ocr_text)
        payload, last_report = None, None

        for iteration in range(1, max_iterations + 1):
            content, duration, error = self._ask(
                provider, ticket, prompt, images,
                step='extract' if iteration == 1 else 'fix',
                iteration=iteration,
                system=SYSTEM_PROMPT, json_schema=TICKET_SCHEMA)

            if error:
                if iteration >= max_iterations:
                    raise UserError(error)
                continue

            candidate = self._parse_json(content)
            if candidate is None:
                prompt = self._build_extraction_prompt(
                    ocr_text,
                    remark=_("Ta réponse précédente n'était pas un JSON "
                             "valide. Renvoie uniquement l'objet JSON."))
                continue

            payload = self._normalize_payload(candidate)
            last_report = self._check_arithmetic(payload)

            if last_report['consistent']:
                payload['_report'] = last_report
                return payload

            if iteration >= max_iterations:
                break

            prompt = self._build_extraction_prompt(
                ocr_text, remark=last_report['message'])

        if payload is None:
            raise UserError(_(
                "Le moteur n'a produit aucune réponse exploitable. "
                "Consultez le journal des échanges IA."))

        payload['_report'] = last_report or {}
        return payload

    @api.model
    def _ask(self, provider, ticket, prompt, images, step, iteration,
             system, json_schema):
        """Appelle le moteur en journalisant systématiquement l'échange.

        ``system`` et ``json_schema`` sont volontairement obligatoires et
        sans valeur par défaut : cette méthode sert aussi bien l'extraction
        du ticket que l'affectation des comptes, deux échanges dont le
        schéma attendu n'a rien à voir. Un défaut implicite vers l'un des
        deux finit tôt ou tard par être imposé à l'autre par erreur.
        """
        try:
            content, duration = provider.ask(
                prompt=prompt,
                system=system,
                images=images,
                json_schema=json_schema,
            )
            self.env['js.ai.log']._record(
                ticket=ticket, provider=provider, step=step,
                iteration=iteration, prompt=prompt, system=system,
                response=content, duration=duration, success=True,
                image_count=len(images))
            return content, duration, None
        except Exception as error:
            self.env['js.ai.log']._record(
                ticket=ticket, provider=provider, step=step,
                iteration=iteration, prompt=prompt, system=system,
                response='', duration=0.0, success=False,
                error=str(error), image_count=len(images))
            return '', 0.0, str(error)

    @api.model
    def _build_extraction_prompt(self, ocr_text, remark=None):
        blocks = [
            "Analyse ce ticket de dépense et renvoie les données au format "
            "JSON demandé."
        ]
        if ocr_text:
            blocks.append(
                "Transcription automatique du ticket (source fiable pour les "
                "chiffres, mais dont la mise en page peut être désordonnée) :"
                "\n-----\n%s\n-----" % ocr_text[:6000])
            blocks.append(
                "Appuie-toi en priorité sur cette transcription pour les "
                "montants, et sur l'image pour la structure des lignes.")
        if remark:
            blocks.append("Correction attendue : %s" % remark)
        return "\n\n".join(blocks)

    @api.model
    def _parse_json(self, content):
        """Extrait un objet JSON d'une réponse éventuellement bavarde."""
        if not content:
            return None

        text = content.strip()
        # Retrait d'un éventuel bloc de code Markdown.
        if text.startswith('```'):
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)

        try:
            return json.loads(text)
        except (ValueError, TypeError):
            pass

        # Repli : on isole la première accolade équilibrée.
        start = text.find('{')
        if start < 0:
            return None
        depth = 0
        for index in range(start, len(text)):
            if text[index] == '{':
                depth += 1
            elif text[index] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:index + 1])
                    except (ValueError, TypeError):
                        return None
        return None

    # ------------------------------------------------------------------
    # Normalisation et contrôle
    # ------------------------------------------------------------------
    @api.model
    def _normalize_payload(self, raw):
        """Convertit la réponse brute en structure typée et fiable."""
        payload = {
            'partner_name': (raw.get('partner_name') or '').strip(),
            'summary': (raw.get('summary') or '').strip(),
            'date': (raw.get('date') or '').strip(),
            'reference': (raw.get('reference') or '').strip(),
            'currency': (raw.get('currency') or '').strip(),
            'total': self._safe_amount(raw.get('total')),
            'total_vat': self._safe_amount(raw.get('total_vat')),
            'lines': [],
            'vat_summary': [],
        }

        for item in raw.get('lines') or []:
            if not isinstance(item, dict):
                continue
            description = (item.get('description') or '').strip()
            amount = self._safe_amount(item.get('amount'))
            if not description and amount is None:
                continue

            quantity = self._safe_amount(item.get('quantity'))
            unit_price = self._safe_amount(item.get('unit_price'))

            # Reconstruction du montant lorsqu'il manque mais que la
            # quantité et le prix unitaire sont lisibles.
            if amount is None and quantity is not None and unit_price is not None:
                amount = quantize(quantity * unit_price)

            payload['lines'].append({
                'description': description or _("Sans libellé"),
                'quantity': quantity if quantity else Decimal('1'),
                'unit_price': unit_price,
                'amount': amount,
                'vat_rate': self._safe_amount(item.get('vat_rate')),
            })

        for item in raw.get('vat_summary') or []:
            if not isinstance(item, dict):
                continue
            payload['vat_summary'].append({
                'rate': self._safe_amount(item.get('rate')),
                'base': self._safe_amount(item.get('base')),
                'amount': self._safe_amount(item.get('amount')),
            })

        return payload

    @api.model
    def _safe_amount(self, value):
        if value in (None, '', False):
            return None
        try:
            return parse_amount(value)
        except AmountParseError:
            return None

    @api.model
    def _check_arithmetic(self, payload):
        """Confronte la somme des lignes au total annoncé.

        C'est le contrôle qui distingue une extraction exploitable d'une
        hallucination : un modèle qui invente un montant produit presque
        toujours une somme incohérente.
        """
        lines = [l for l in payload['lines'] if l['amount'] is not None]
        total_lines = quantize(sum((l['amount'] for l in lines), Decimal('0')))
        declared = payload.get('total')

        report = {
            'consistent': False,
            'total_lines': total_lines,
            'total_declared': declared,
            'gap': None,
            'message': '',
        }

        if declared is None:
            report['message'] = _(
                "Le total du ticket n'a pas été trouvé. Relis le bas du "
                "ticket et indique le montant total exact.")
            return report

        gap = quantize(total_lines - declared)
        report['gap'] = gap

        if gap == Decimal('0.00'):
            report['consistent'] = True
            return report

        report['message'] = _(
            "La somme des lignes que tu as extraites vaut %(sum)s alors que "
            "le total du ticket est %(total)s, soit un écart de %(gap)s. "
            "Relis attentivement le ticket : une ligne est probablement "
            "manquante, en trop, ou mal recopiée.",
            sum=total_lines, total=declared, gap=gap)
        return report

    # ------------------------------------------------------------------
    # Report sur le ticket
    # ------------------------------------------------------------------
    @api.model
    def _apply_to_ticket(self, ticket, payload, provider):
        values = {
            'ai_provider_id': provider.id,
            'ai_raw_json': json.dumps(
                payload, indent=2, default=str, ensure_ascii=False),
        }

        if payload.get('partner_name'):
            values['partner_name_raw'] = payload['partner_name']
            partner = self.env['js.depense.partner.alias']._find_partner(
                payload['partner_name'], ticket.company_id)
            if partner and not ticket.partner_id:
                values['partner_id'] = partner.id

        parsed_date = self._parse_date(payload.get('date'))
        if parsed_date:
            values['ticket_date'] = parsed_date

        if payload.get('reference'):
            values['reference'] = payload['reference'][:64]
        if payload.get('total') is not None:
            values['amount_total_ticket'] = float(payload['total'])
        if not ticket.description:
            if payload.get('summary'):
                values['description'] = ("TICKET %s" % payload['summary'])[:128]
            elif payload.get('partner_name'):
                values['description'] = payload['partner_name'][:128]

        report = payload.get('_report') or {}
        values['ai_confidence'] = 0.9 if report.get('consistent') else 0.4

        ticket.with_context(js_ai_proposal=True).write(values)

        ticket.line_ids.unlink()
        self._create_lines(ticket, payload, provider)
        ticket._rebuild_tax_lines()
        self._fill_declared_vat(ticket, payload)

        if ticket.state == 'draft':
            ticket.state = 'to_validate'

        if not report.get('consistent'):
            ticket.message_post(body=_(
                "<p><b>Analyse à vérifier.</b></p><p>%s</p>",
                report.get('message') or _("Le contrôle n'a pas abouti.")))
        return ticket

    @api.model
    def _parse_date(self, text):
        if not text:
            return None
        text = text.strip()
        patterns = [
            (r'^(\d{4})-(\d{2})-(\d{2})$', (1, 2, 3)),
            (r'^(\d{2})[./-](\d{2})[./-](\d{4})$', (3, 2, 1)),
            (r'^(\d{2})[./-](\d{2})[./-](\d{2})$', None),
        ]
        for pattern, order in patterns:
            match = re.match(pattern, text)
            if not match:
                continue
            try:
                if order is None:
                    day, month, year = match.groups()
                    year = int(year)
                    year += 2000 if year < 70 else 1900
                    return '%04d-%02d-%02d' % (year, int(month), int(day))
                year, month, day = (match.group(i) for i in order)
                return '%04d-%02d-%02d' % (int(year), int(month), int(day))
            except (ValueError, TypeError):
                continue
        return None

    @api.model
    def _create_lines(self, ticket, payload, provider):
        """Crée les lignes et leur affecte un compte."""
        line_model = self.env['js.depense.ticket.line']
        rule_model = self.env['js.depense.account.rule']
        auto_apply = ticket.company_id.js_depense_auto_apply_rules

        unresolved = []
        created = []

        for index, item in enumerate(payload['lines']):
            amount = item['amount'] or Decimal('0')
            values = {
                'ticket_id': ticket.id,
                'sequence': (index + 1) * 10,
                'name': item['description'],
                'quantity': 1.0,
                'price_unit': float(amount),
                'ai_confidence': 0.9 if payload.get(
                    '_report', {}).get('consistent') else 0.4,
            }

            key = normalize_label(item['description'])
            rule = rule_model._find_best(
                key, ticket.partner_id, ticket.company_id) if auto_apply \
                else rule_model.browse()

            if rule:
                values.update({
                    'account_id': rule.account_id.id,
                    'account_source': 'learned',
                    'account_rule_id': rule.id,
                })
                taxes = self._resolve_taxes(
                    rule.account_id, item['vat_rate'], ticket.company_id,
                    preferred=rule.tax_ids)
                values['tax_ids'] = [(6, 0, taxes.ids)]
                rule.mark_used()
            else:
                unresolved.append((index, item))

            created.append(line_model.create(values))

        if unresolved:
            self._resolve_accounts_with_ai(
                ticket, provider, created, unresolved, payload)
        return created

    @api.model
    def _fill_declared_vat(self, ticket, payload):
        """Reporte la TVA imprimée dans le récapitulatif, taux par taux."""
        summary = payload.get('vat_summary') or []
        if not summary:
            return

        for tax_line in ticket.tax_line_ids:
            rate = tax_line.tax_id.amount
            for entry in summary:
                if entry['rate'] is None or entry['amount'] is None:
                    continue
                if abs(float(entry['rate']) - rate) < 0.01:
                    tax_line.tax_amount_ticket = float(entry['amount'])
                    break

    # ------------------------------------------------------------------
    # Affectation comptable
    # ------------------------------------------------------------------
    @api.model
    def _resolve_taxes(self, account, vat_rate, company, preferred=None):
        """Choisit la taxe applicable à une ligne.

        Le taux lu sur le ticket prime : un même compte peut recevoir du
        8.1 % et du 2.6 % selon les articles. À défaut de taux lisible, la
        taxe par défaut du compte s'applique.
        """
        tax_model = self.env['account.tax']
        account_taxes = account.tax_ids.filtered(
            lambda t: t.type_tax_use == 'purchase'
            and (not t.company_id or t.company_id == company))

        if vat_rate is None:
            if preferred:
                return preferred
            return account_taxes

        rate = float(vat_rate)

        # 1. Une taxe du compte correspond exactement au taux lu.
        matching = account_taxes.filtered(
            lambda t: abs(t.amount - rate) < 0.01)
        if matching:
            return matching[:1]

        if not account_taxes:
            return tax_model.browse()

        # 2. Sinon, on cherche dans toutes les taxes d'achat en conservant
        #    le mode d'inclusion de la taxe par défaut du compte, afin de ne
        #    pas basculer involontairement de TTC à HT.
        price_include = account_taxes[0].price_include
        candidates = tax_model.search([
            ('type_tax_use', '=', 'purchase'),
            ('company_id', '=', company.id),
            ('amount_type', '=', 'percent'),
        ])
        same_rate = candidates.filtered(
            lambda t: abs(t.amount - rate) < 0.01
            and t.price_include == price_include)
        if same_rate:
            return same_rate[:1]

        return account_taxes[:1]

    @api.model
    def _resolve_accounts_with_ai(self, ticket, provider, lines, unresolved,
                                  payload):
        """Soumet au moteur les seules lignes restées sans compte.

        Une liste restreinte de comptes plausibles lui est proposée : lui
        soumettre le plan comptable entier dégraderait la qualité de la
        réponse et allongerait inutilement le traitement.
        """
        candidates = self._build_account_shortlist(ticket, unresolved)
        if not candidates:
            return

        descriptions = [item['description'] for _index, item in unresolved]
        prompt = self._build_accounts_prompt(
            descriptions, candidates, ticket.partner_id)

        content, duration, error = self._ask(
            provider, ticket, prompt, images=[], step='accounts', iteration=1,
            system=ACCOUNTS_SYSTEM_PROMPT, json_schema=None)
        if error:
            return

        data = self._parse_json(content)
        if not isinstance(data, dict):
            return

        mapping = data.get('assignments') or {}
        by_code = {account.code: account for account in candidates}

        for position, (index, item) in enumerate(unresolved):
            line = lines[index]
            code = mapping.get(item['description']) or mapping.get(str(position))
            if not code:
                continue
            account = by_code.get(str(code).strip())
            if not account:
                continue
            taxes = self._resolve_taxes(
                account, item['vat_rate'], ticket.company_id)
            line.with_context(js_ai_proposal=True).write({
                'account_id': account.id,
                'account_source': 'ai',
                'ai_account_hint': str(code),
                'tax_ids': [(6, 0, taxes.ids)],
            })

    @api.model
    def _build_account_shortlist(self, ticket, unresolved, limit=200):
        """Comptes plausibles : historique du fournisseur, puis mots-clés.

        Le plan comptable des charges tient dans les plages 4000-4999
        (achats/marchandises) et 6000-6999 (charges d'exploitation), plus le
        compte 5200 utilisé spécifiquement pour les tickets de dépense : ces
        comptes sont donc systématiquement proposés à l'IA, quel que soit
        leur `account_type`, plutôt que déduits d'une correspondance
        approximative sur le libellé.
        """
        account_model = self.env['account.account']
        company = ticket.company_id
        shortlist = account_model.browse()

        # 1. Comptes déjà employés avec ce fournisseur sur des tickets
        #    antérieurs : c'est le signal le plus fort.
        if ticket.partner_id:
            previous = self.env['js.depense.ticket.line'].search([
                ('ticket_id.partner_id', '=', ticket.partner_id.id),
                ('ticket_id.state', 'in', ('validated', 'posted')),
                ('company_id', '=', company.id),
            ], limit=100)
            shortlist |= previous.mapped('account_id')

            moves = self.env['account.move.line'].search([
                ('partner_id', '=', ticket.partner_id.id),
                ('company_id', '=', company.id),
                ('account_id.account_type', 'in',
                 ('expense', 'expense_direct_cost', 'asset_fixed')),
            ], limit=100)
            shortlist |= moves.mapped('account_id')

        # 2. Le plan comptable des charges proprement dit : toute la plage
        #    4000-4999, toute la plage 6000-6999, et le compte 5200.
        shortlist |= account_model.search([
            ('deprecated', '=', False),
            ('company_ids', 'in', company.id),
            '|', '|',
            ('code', '=like', '4%'),
            ('code', '=like', '6%'),
            ('code', '=', '5200'),
        ])

        return shortlist[:limit]

    @api.model
    def _build_accounts_prompt(self, descriptions, accounts, partner):
        catalogue = "\n".join(
            "%s - %s" % (account.code, account.name) for account in accounts)
        items = "\n".join("- %s" % description for description in descriptions)
        supplier = partner.display_name if partner else _("inconnu")

        return (
            "Fournisseur : %(supplier)s\n\n"
            "Comptes comptables disponibles :\n%(catalogue)s\n\n"
            "Affecte à chacun des libellés suivants le code de compte le "
            "plus approprié :\n%(items)s\n\n"
            "Réponds uniquement par un JSON de la forme :\n"
            '{\"assignments\": {\"libellé exact\": \"code\"}}\n'
            "N'utilise que des codes figurant dans la liste ci-dessus. "
            "Si aucun compte ne convient, omets le libellé."
            % {'supplier': supplier, 'catalogue': catalogue, 'items': items}
        )
