# -*- coding: utf-8 -*-
"""Prétraitement des images et reconnaissance de texte.

L'OCR n'a pas vocation à structurer le ticket : il fournit au modèle de
langage une transcription **déterministe** des caractères imprimés. Les
montants sont ainsi lus sur une source vérifiable plutôt que devinés par
le réseau de neurones, ce qui réduit fortement le risque d'erreur sur les
chiffres.
"""

import io
import logging
import subprocess
import tempfile
import os

_logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageOps, ImageFilter
except ImportError:  # pragma: no cover
    Image = None
    _logger.warning("Pillow est absent : le prétraitement d'image est désactivé.")

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None

# Largeur maximale transmise au modèle. Au-delà, le gain de lisibilité est
# nul alors que le coût de traitement augmente sensiblement.
MAX_WIDTH = 1600
OCR_LANGUAGES = 'fra+deu+eng'


def is_pdf(data):
    return bool(data) and data[:5] == b'%PDF-'


def pdf_to_images(data, max_pages=5):
    """Convertit un PDF en images PNG via ``pdftoppm``.

    ``pdftoppm`` fait partie de poppler-utils, généralement présent sur un
    serveur Odoo (utilisé pour le rendu des rapports).
    """
    images = []
    tmpdir = tempfile.mkdtemp(prefix='js_depenses_')
    pdf_path = os.path.join(tmpdir, 'source.pdf')
    try:
        with open(pdf_path, 'wb') as handle:
            handle.write(data)

        subprocess.run(
            ['pdftoppm', '-png', '-r', '200',
             '-f', '1', '-l', str(max_pages),
             pdf_path, os.path.join(tmpdir, 'page')],
            check=True, capture_output=True, timeout=120)

        for filename in sorted(os.listdir(tmpdir)):
            if filename.startswith('page') and filename.endswith('.png'):
                with open(os.path.join(tmpdir, filename), 'rb') as handle:
                    images.append(handle.read())
    except FileNotFoundError:
        _logger.warning("pdftoppm introuvable : PDF non converti.")
    except Exception as error:
        _logger.warning("Conversion PDF impossible : %s", error)
    finally:
        _cleanup(tmpdir)
    return images


def _cleanup(path):
    try:
        for name in os.listdir(path):
            os.unlink(os.path.join(path, name))
        os.rmdir(path)
    except OSError:
        pass


def preprocess(data, grayscale=True, enhance=True):
    """Redresse, réduit et contraste une photo de ticket.

    Les tickets photographiés au téléphone sont souvent surdimensionnés,
    peu contrastés et mal orientés ; ces trois corrections améliorent
    nettement la reconnaissance.
    """
    if Image is None or not data:
        return data

    try:
        image = Image.open(io.BytesIO(data))

        # Respect de l'orientation EXIF : une photo prise en portrait
        # arrive fréquemment couchée.
        image = ImageOps.exif_transpose(image)

        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')

        if image.width > MAX_WIDTH:
            ratio = MAX_WIDTH / float(image.width)
            image = image.resize(
                (MAX_WIDTH, int(image.height * ratio)), Image.LANCZOS)

        if grayscale:
            image = ImageOps.grayscale(image)
        if enhance:
            image = ImageOps.autocontrast(image)
            image = image.filter(ImageFilter.SHARPEN)

        buffer = io.BytesIO()
        image.convert('RGB').save(buffer, format='JPEG', quality=88)
        return buffer.getvalue()
    except Exception as error:
        _logger.warning("Prétraitement impossible : %s", error)
        return data


def extract_text(data, languages=OCR_LANGUAGES):
    """Transcription du texte imprimé. Chaîne vide si l'OCR est indisponible."""
    if not data:
        return ''

    if pytesseract is not None and Image is not None:
        try:
            image = Image.open(io.BytesIO(data))
            return pytesseract.image_to_string(image, lang=languages)
        except Exception as error:
            _logger.info("pytesseract en échec (%s), repli sur l'exécutable.",
                         error)

    return _extract_text_cli(data, languages)


def _extract_text_cli(data, languages):
    """Repli : appel direct à l'exécutable ``tesseract``."""
    tmpdir = tempfile.mkdtemp(prefix='js_depenses_ocr_')
    image_path = os.path.join(tmpdir, 'image.jpg')
    output_base = os.path.join(tmpdir, 'out')
    try:
        with open(image_path, 'wb') as handle:
            handle.write(data)

        subprocess.run(
            ['tesseract', image_path, output_base, '-l', languages, '--psm', '6'],
            check=True, capture_output=True, timeout=120)

        with open(output_base + '.txt', 'r', encoding='utf-8') as handle:
            return handle.read()
    except FileNotFoundError:
        _logger.info("Tesseract n'est pas installé sur ce serveur.")
    except Exception as error:
        _logger.warning("OCR impossible : %s", error)
    finally:
        _cleanup(tmpdir)
    return ''


def prepare_documents(raw_documents, use_ocr=True):
    """Prépare une liste de pièces jointes pour l'analyse.

    :param raw_documents: liste de contenus binaires (images ou PDF)
    :return: ``(images, texte_ocr)``
    """
    images, texts = [], []

    for data in raw_documents or []:
        if not data:
            continue
        pages = pdf_to_images(data) if is_pdf(data) else [data]
        for page in pages:
            processed = preprocess(page)
            images.append(processed)
            if use_ocr:
                text = extract_text(processed)
                if text and text.strip():
                    texts.append(text.strip())

    return images, "\n\n".join(texts)
