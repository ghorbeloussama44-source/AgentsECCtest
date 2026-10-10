
#!/usr/bin/env python3
"""
mise_en_page.py — Assemblage du livre PDF pour la collection.
Usage : python3 mise_en_page.py plan.json dossier_chapitres sortie.pdf [--corps 11] [--auteur …] [--annee …] [--collection …]
Format : 6x9 pouces (152,4 x 228,6 mm), police Times (WinAnsi).
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from reportlab.lib.pagesizes import inch
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.colors import HexColor
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer,
    PageBreak, NextPageTemplate, HRFlowable, Flowable,
    Table, TableStyle, KeepTogether,
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.pdfbase.pdfmetrics import registerFontFamily

# ─── Constantes géométriques (6 x 9 pouces) ─────────────────────────────────

PAGE_W = 6 * inch   # 432 pt = 152,4 mm
PAGE_H = 9 * inch   # 648 pt = 228,6 mm

MARGIN_LR = 16 * mm   # marges gauche / droite (intérieure / extérieure)
MARGIN_TB = 18 * mm   # marges haut / bas

BODY_DEFAULT = 10.0
LEADING_DEFAULT = 12.5

# ─── Polices : famille Times intégrée à ReportLab (WinAnsi) ─────────────────

FONT_SERIF = "Times-Roman"
FONT_SERIF_BOLD = "Times-Bold"
FONT_SERIF_ITALIC = "Times-Italic"
FONT_SERIF_BOLDITALIC = "Times-BoldItalic"

registerFontFamily(
    "Times",
    normal=FONT_SERIF,
    bold=FONT_SERIF_BOLD,
    italic=FONT_SERIF_ITALIC,
    boldItalic=FONT_SERIF_BOLDITALIC,
)

# ─── Assainissement WinAnsi ──────────────────────────────────────────────────

_WINANSI_REPLACEMENTS = [
    ('\u202f', '\u00a0'),   # fine no-break space -> no-break space
    ('\u2009', '\u00a0'),   # thin space -> no-break space
    ('\u200a', ' '),        # hair space
    ('\u2192', '->'),       # arrow right
    ('\u2190', '<-'),       # arrow left
    ('\u2194', '<->'),      # arrow both
    ('\u2248', 'env.'),     # approx
    ('\u2260', '!='),       # not equal
    ('\u2264', '<='),       # lte
    ('\u2265', '>='),       # gte
    ('\u2713', '-'),        # check
    ('\u2714', '-'),        # heavy check
    ('\u2717', 'x'),        # cross
    ('\u2718', 'x'),        # heavy cross
    ('\u25cf', '-'),        # black circle
    ('\u25cb', 'o'),        # white circle
    ('\u2022', '-'),        # bullet
    ('\u2023', '-'),        # triangular bullet
    ('\u2043', '-'),        # hyphen bullet
    ('\u00a0', '\u00a0'),   # NBSP stays
    ('\ufeff', ''),         # BOM
    ('\u200b', ''),         # zero-width space
    ('\u200c', ''),         # ZWNJ
    ('\u200d', ''),         # ZWJ
]

def sanitize_winansi(text: str) -> str:
    """Remplace les caractères hors WinAnsi par des équivalents sûrs."""
    for old, new in _WINANSI_REPLACEMENTS:
        text = text.replace(old, new)
    out = []
    for ch in text:
        cp = ord(ch)
        if cp <= 0xFF:
            out.append(ch)
        elif ch in '\u20ac\u201a\u0192\u201e\u2026\u2020\u2021\u02c6\u2030' \
                   '\u0160\u2039\u0152\u017d\u2018\u2019\u201c\u201d' \
                   '\u2022\u2013\u2014\u02dc\u2122\u0161\u203a\u0153\u017e\u0178':
            out.append(ch)
        else:
            out.append(' ')
    return ''.join(out)

# ─── Conversion Markdown → XML ReportLab ─────────────────────────────────────

def escape_xml(text: str) -> str:
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text

def _is_meaningful(content: str) -> bool:
    stripped = content.strip()
    if not stripped:
        return False
    if re.match(r'^[\s_\-./\\,;:!?\'"()\[\]{}|~`^+=*#]+$', stripped):
        return False
    return True

def inline_markup(text: str) -> str:
    def _sub(pattern, tag_open, tag_close, txt):
        def replacer(m):
            content = m.group(1)
            if not _is_meaningful(content):
                return m.group(0)
            return tag_open + content + tag_close
        return re.sub(pattern, replacer, txt)

    text = _sub(r'\*\*\*(.+?)\*\*\*', '<b><i>', '</i></b>', text)
    text = _sub(r'___(.+?)___', '<b><i>', '</i></b>', text)
    text = _sub(r'\*\*(.+?)\*\*', '<b>', '</b>', text)
    text = _sub(r'__(.+?)__', '<b>', '</b>', text)
    text = _sub(r'\*(.+?)\*', '<i>', '</i>', text)
    text = _sub(r'(?<!\w)_(.+?)_(?!\w)', '<i>', '</i>', text)
    return text

def md_to_xml(text: str) -> str:
    text = sanitize_winansi(text)
    text = escape_xml(text)
    text = inline_markup(text)
    return text

def strip_think_tags(text: str) -> str:
    return re.sub(r'', '', text, flags=re.DOTALL | re.IGNORECASE)

def safe_paragraph(text: str, style) -> Paragraph:
    """Crée un Paragraph avec filet de sécurité anti-exception."""
    try:
        return Paragraph(text, style)
    except Exception:
        pass
    try:
        plain = re.sub(r'<[^>]*>', '', text)
        return Paragraph(plain, style)
    except Exception:
        pass
    try:
        raw = re.sub(r'<[^>]*>', '', text)
        raw = raw.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        raw = escape_xml(raw)
        return Paragraph(raw, style)
    except Exception:
        return Paragraph("", style)

def strip_chapter_heading(md_text: str, chap_num: int) -> str:
    """
    Supprime la première ligne ## si elle reprend le numéro du chapitre.
    """
    lines = md_text.split('\n')
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if re.match(
            r'^##\s+[Cc]hapitre\s+' + str(chap_num) + r'[\s\-—–:.]',
            line.strip()
        ):
            return '\n'.join(lines[:i] + lines[i + 1:])
        break
    return md_text

def parse_markdown(md_text: str, styles: dict) -> list:
    """Convertit le Markdown en flowables."""
    flowables = []
    md_text = strip_think_tags(md_text)
    lines = md_text.split('\n')
    i = 0
    in_code_block = False
    in_list = False
    list_items = []

    def flush_list():
        nonlocal in_list, list_items
        if list_items:
            for item_text in list_items:
                flowables.append(safe_paragraph(item_text, styles['list_item']))
            flowables.append(Spacer(1, 4))
        list_items = []
        in_list = False

    code_fence = '`' * 3

    while i < len(lines):
        line = lines[i]

        if line.strip().startswith(code_fence):
            if in_code_block:
                in_code_block = False
            else:
                in_code_block = True
                flush_list()
            i += 1
            continue
        if in_code_block:
            i += 1
            continue

        stripped = line.strip()

        if not stripped:
            flush_list()
            i += 1
            continue

        # Séparateur horizontal
        if re.match(r'^-{3,}$|^\*{3,}$|^_{3,}$', stripped):
            flush_list()
            flowables.append(Spacer(1, 6))
            flowables.append(HRFlowable(width="100%", thickness=0.5,
                                        color=HexColor("#999999")))
            flowables.append(Spacer(1, 6))
            i += 1
            continue

        # Titres ##, ###, ####
        heading_match = re.match(r'^(#{2,4})\s+(.*)', stripped)
        if heading_match:
            flush_list()
            level = len(heading_match.group(1))
            title_text = md_to_xml(heading_match.group(2))
            key = f'h{level}'
            if key in styles:
                flowables.append(safe_paragraph(title_text, styles[key]))
            else:
                flowables.append(safe_paragraph(title_text, styles['body']))
            i += 1
            continue

        # Titre #
        h1_match = re.match(r'^#\s+(.*)', stripped)
        if h1_match:
            flush_list()
            flowables.append(safe_paragraph(md_to_xml(h1_match.group(1)),
                                            styles['h2']))
            i += 1
            continue

        # Citation
        if stripped.startswith('>'):
            flush_list()
            quote_text = md_to_xml(stripped.lstrip('> '))
            flowables.append(safe_paragraph(quote_text, styles['quote']))
            i += 1
            continue

        # Tableau
        if stripped.startswith('|') and stripped.endswith('|'):
            flush_list()
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                table_lines.append(lines[i].strip())
                i += 1
            flowables.extend(build_table(table_lines, styles))
            continue

        # Liste non ordonnée
        ul_match = re.match(r'^(\s*)[-*+]\s+(.*)', line)
        if ul_match:
            if not in_list:
                flush_list()
                in_list = True
            item_text = "\u2022 " + md_to_xml(ul_match.group(2))
            list_items.append(item_text)
            i += 1
            continue

        # Liste ordonnée
        ol_match = re.match(r'^(\s*)\d+[.)]\s+(.*)', line)
        if ol_match:
            if not in_list:
                flush_list()
                in_list = True
            num = re.match(r'^(\s*)(\d+)[.)]\s+(.*)', line)
            item_text = f"{num.group(2)}. " + md_to_xml(num.group(3))
            list_items.append(item_text)
            i += 1
            continue

        # Paragraphe normal
        flush_list()
        para_text = md_to_xml(stripped)
        if para_text.strip():
            flowables.append(safe_paragraph(para_text, styles['body']))
        i += 1

    flush_list()
    return flowables

def build_table(table_lines: list, styles: dict) -> list:
    rows = []
    for tl in table_lines:
        if re.match(r'^\|[\s\-:|]+\|$', tl):
            continue
        cells = [c.strip() for c in tl.strip('|').split('|')]
        cells = [md_to_xml(c) for c in cells]
        rows.append(cells)
    if not rows:
        return []
    ncols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < ncols:
            r.append('')
    table_data = [[safe_paragraph(c, styles['table_cell']) for c in row]
                  for row in rows]
    avail_w = PAGE_W - 2 * MARGIN_LR
    col_w = avail_w / max(ncols, 1)
    t = Table(table_data, colWidths=[col_w] * ncols)
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), FONT_SERIF),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.4, HexColor("#666666")),
        ('BACKGROUND', (0, 0), (-1, 0), HexColor("#EEEEEE")),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    return [Spacer(1, 6), t, Spacer(1, 6)]

# ─── Styles ───────────────────────────────────────────────────────────────────

def make_styles(corps: float, leading: float) -> dict:
    s = {}
    s['body'] = ParagraphStyle(
        'Body', fontName=FONT_SERIF, fontSize=corps, leading=leading,
        alignment=TA_JUSTIFY, spaceAfter=corps * 0.55, spaceBefore=0,
        firstLineIndent=corps * 1.5,
    )
    s['h2'] = ParagraphStyle(
        'H2', fontName=FONT_SERIF_BOLD,
        fontSize=corps * 1.3, leading=corps * 1.65,
        alignment=TA_LEFT, spaceBefore=corps * 1.1, spaceAfter=corps * 0.5,
    )
    s['h3'] = ParagraphStyle(
        'H3', fontName=FONT_SERIF_BOLD,
        fontSize=corps * 1.12, leading=corps * 1.45,
        alignment=TA_LEFT, spaceBefore=corps * 0.9, spaceAfter=corps * 0.4,
    )
    s['h4'] = ParagraphStyle(
        'H4', fontName=FONT_SERIF_ITALIC,
        fontSize=corps * 1.05, leading=corps * 1.35,
        alignment=TA_LEFT, spaceBefore=corps * 0.7, spaceAfter=corps * 0.35,
    )
    s['quote'] = ParagraphStyle(
        'Quote', fontName=FONT_SERIF_ITALIC,
        fontSize=corps * 0.95, leading=leading * 0.95,
        alignment=TA_JUSTIFY, leftIndent=20, rightIndent=10,
        spaceBefore=corps * 0.5, spaceAfter=corps * 0.5,
        textColor=HexColor("#444444"),
    )
    s['list_item'] = ParagraphStyle(
        'ListItem', fontName=FONT_SERIF, fontSize=corps, leading=leading,
        alignment=TA_JUSTIFY, leftIndent=18, spaceAfter=corps * 0.3,
        bulletIndent=6,
    )
    s['chap_title'] = ParagraphStyle(
        'ChapTitle', fontName=FONT_SERIF_BOLD,
        fontSize=corps * 1.6, leading=corps * 2.0,
        alignment=TA_LEFT, spaceBefore=corps * 0.5, spaceAfter=corps * 1.0,
    )
    s['part_title'] = ParagraphStyle(
        'PartTitle', fontName=FONT_SERIF_BOLD,
        fontSize=20, leading=26, alignment=TA_CENTER,
        spaceBefore=0, spaceAfter=12,
    )
    s['sect_title'] = ParagraphStyle(
        'SectTitle', fontName=FONT_SERIF_BOLD,
        fontSize=corps * 1.6, leading=corps * 2.0,
        alignment=TA_LEFT, spaceBefore=corps * 0.5, spaceAfter=corps * 1.0,
    )
    # Page de titre
    s['title_page'] = ParagraphStyle(
        'TitlePage', fontName=FONT_SERIF_BOLD,
        fontSize=24, leading=30, alignment=TA_CENTER, spaceAfter=12,
    )
    s['subtitle'] = ParagraphStyle(
        'Subtitle', fontName=FONT_SERIF_ITALIC,
        fontSize=14, leading=18, alignment=TA_CENTER, spaceAfter=8,
    )
    s['author_name'] = ParagraphStyle(
        'AuthorName', fontName=FONT_SERIF_BOLD,
        fontSize=16, leading=21, alignment=TA_CENTER, spaceAfter=8,
    )
    s['collection_name'] = ParagraphStyle(
        'CollectionName', fontName=FONT_SERIF_ITALIC,
        fontSize=11, leading=14, alignment=TA_CENTER,
        textColor=HexColor("#555555"),
    )
    # Copyright
    s['copyright'] = ParagraphStyle(
        'Copyright', fontName=FONT_SERIF,
        fontSize=8.5, leading=11.5, alignment=TA_LEFT,
        spaceAfter=8, textColor=HexColor("#333333"),
    )
    # TOC avec retrait à droite pour le numéro de page
    s['toc_level0'] = ParagraphStyle(
        'TOCLevel0', fontName=FONT_SERIF_BOLD,
        fontSize=corps, leading=leading, spaceBefore=7, spaceAfter=2,
        rightIndent=28,
    )
    s['toc_level1'] = ParagraphStyle(
        'TOCLevel1', fontName=FONT_SERIF,
        fontSize=corps * 0.92, leading=leading * 0.92,
        leftIndent=14, spaceBefore=1, spaceAfter=1,
        rightIndent=28,
    )
    s['bib_entry'] = ParagraphStyle(
        'BibEntry', fontName=FONT_SERIF, fontSize=corps * 0.92,
        leading=leading * 0.92, alignment=TA_JUSTIFY,
        spaceAfter=corps * 0.7, leftIndent=12,
    )
    s['warning'] = ParagraphStyle(
        'Warning', fontName=FONT_SERIF, fontSize=corps, leading=leading,
        alignment=TA_JUSTIFY, spaceAfter=corps,
    )
    s['table_cell'] = ParagraphStyle(
        'TableCell', fontName=FONT_SERIF, fontSize=8.5, leading=11,
        alignment=TA_LEFT,
    )
    return s

# ─── Flowable invisible : mise à jour du titre courant ───────────────────────

class RunningTitle(Flowable):
    """Flowable de hauteur nulle qui met à jour le titre courant du doc."""
    def __init__(self, title: str):
        super().__init__()
        self.title = title
        self.width = 0
        self.height = 0

    def draw(self):
        pass

# ─── Template de document ────────────────────────────────────────────────────

class BookDocTemplate(BaseDocTemplate):
    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)
        self._current_title = ""

    def afterFlowable(self, flowable):
        if isinstance(flowable, RunningTitle):
            self._current_title = flowable.title
            return

        if isinstance(flowable, Paragraph):
            style_name = flowable.style.name
            text = flowable.getPlainText()
            if style_name == 'PartTitle':
                self.notify('TOCEntry', (0, text, self.page))
            elif style_name == 'SectTitle':
                self.notify('TOCEntry', (0, text, self.page))
            elif style_name == 'ChapTitle':
                self.notify('TOCEntry', (1, text, self.page))

# ─── En-tête / pied de page ──────────────────────────────────────────────────

def make_header_footer(annee: int, auteur: str):
    """Fabrique la fonction header_footer avec les bons paramètres."""
    def header_footer(canvas, doc):
        canvas.saveState()
        page_num = canvas.getPageNumber()

        # Pied : numéro de page centré + mention copyright
        canvas.setFont(FONT_SERIF, 8.5)
        canvas.drawCentredString(PAGE_W / 2, 10 * mm, str(page_num))

        # Mention copyright en petit sous le numéro
        canvas.setFont(FONT_SERIF, 6.5)
        canvas.drawCentredString(
            PAGE_W / 2, 7 * mm,
            f"\u00a9 {annee} {auteur}")

        # En-tête : titre courant (à partir de la page 4)
        if page_num > 3 and doc._current_title:
            canvas.setFont(FONT_SERIF_ITALIC, 8)
            canvas.drawCentredString(PAGE_W / 2, PAGE_H - 11 * mm,
                                     doc._current_title)
            canvas.setStrokeColor(HexColor("#AAAAAA"))
            canvas.setLineWidth(0.4)
            canvas.line(MARGIN_LR, PAGE_H - 13 * mm,
                        PAGE_W - MARGIN_LR, PAGE_H - 13 * mm)
        canvas.restoreState()
    return header_footer

def no_header_footer(canvas, doc):
    """Pages sans en-tête ni pied (page de titre, pages de partie)."""
    pass

# ─── Lecture fichier ─────────────────────────────────────────────────────────

def read_file_safe(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except Exception as e:
        print(f"\u26a0 Erreur lecture {path} : {e}", file=sys.stderr)
        return ""

# ─── Construction du livre ───────────────────────────────────────────────────

def build_book(plan_path: str, chapitres_dir: str, output_path: str,
               corps: float, auteur: str, annee: int, collection: str):
    leading = LEADING_DEFAULT if corps == BODY_DEFAULT else corps * 1.25
    styles = make_styles(corps, leading)

    with open(plan_path, 'r', encoding='utf-8') as f:
        plan = json.load(f)

    titre = plan.get("titre", "Livre")
    sous_titre = plan.get("sous_titre", "")
    bibliographie = plan.get("bibliographie", [])
    parties = plan.get("parties", [])

    # Frames
    frame_w = PAGE_W - 2 * MARGIN_LR
    frame_h = PAGE_H - 2 * MARGIN_TB

    frame_main = Frame(MARGIN_LR, MARGIN_TB, frame_w, frame_h, id='main')
    frame_blank = Frame(MARGIN_LR, MARGIN_TB, frame_w, frame_h, id='blank')

    doc = BookDocTemplate(
        output_path,
        pagesize=(PAGE_W, PAGE_H),
        leftMargin=MARGIN_LR,
        rightMargin=MARGIN_LR,
        topMargin=MARGIN_TB,
        bottomMargin=MARGIN_TB,
        title=titre,
        author=auteur,
        subject=sous_titre,
        creator=collection,
    )

    header_footer_fn = make_header_footer(annee, auteur)

    tmpl_content = PageTemplate(id='content', frames=[frame_main],
                                onPage=header_footer_fn)
    tmpl_blank = PageTemplate(id='blank', frames=[frame_blank],
                              onPage=no_header_footer)
    doc.addPageTemplates([tmpl_blank, tmpl_content])

    story = []

    # ── Page de titre ──────────────────────────────────────────────────────
    story.append(NextPageTemplate('blank'))
    story.append(Spacer(1, 50 * mm))
    story.append(safe_paragraph(escape_xml(sanitize_winansi(titre)),
                                styles['title_page']))
    if sous_titre:
        story.append(Spacer(1, 8))
        story.append(safe_paragraph(escape_xml(sanitize_winansi(sous_titre)),
                                    styles['subtitle']))
    story.append(Spacer(1, 20 * mm))
    story.append(safe_paragraph(escape_xml(sanitize_winansi(auteur)),
                                styles['author_name']))
    story.append(Spacer(1, 55 * mm))
    story.append(safe_paragraph(escape_xml(sanitize_winansi(collection)),
                                styles['collection_name']))
    story.append(PageBreak())

    # ── Page de copyright (verso de la page de titre) ──────────────────────
    story.append(safe_paragraph(
        f"\u00a9 {annee} {auteur}. Tous droits r\u00e9serv\u00e9s.",
        styles['copyright']))
    story.append(safe_paragraph(
        "Aucune partie de cet ouvrage ne peut \u00eatre reproduite, "
        "stock\u00e9e ou transmise, sous quelque forme ou par quelque "
        "moyen que ce soit, sans l\u2019autorisation \u00e9crite "
        "pr\u00e9alable de l\u2019auteur, sauf courtes citations dans "
        "le cadre d\u2019une critique ou d\u2019une recension, "
        "conform\u00e9ment aux lois sur la propri\u00e9t\u00e9 "
        "intellectuelle.",
        styles['copyright']))
    story.append(safe_paragraph(
        f"Ouvrage con\u00e7u et dirig\u00e9 par {auteur}, "
        "r\u00e9dig\u00e9 avec l\u2019assistance d\u2019outils "
        "d\u2019intelligence artificielle.",
        styles['copyright']))
    story.append(safe_paragraph(
        f"{collection} \u2014 Premi\u00e8re \u00e9dition, {annee}.",
        styles['copyright']))
    story.append(PageBreak())

    # ── Page d'avertissement ───────────────────────────────────────────────
    story.append(Spacer(1, 22 * mm))
    warn_title_style = ParagraphStyle(
        'WarnTitle', fontName=FONT_SERIF_BOLD,
        fontSize=14, leading=18, alignment=TA_CENTER, spaceAfter=14,
    )
    story.append(safe_paragraph("Avertissement", warn_title_style))
    story.append(safe_paragraph(
        "Le pr\u00e9sent ouvrage est fourni \u00e0 titre strictement "
        "\u00e9ducatif et informatif. Il ne constitue en aucun cas un "
        "conseil financier personnalis\u00e9, une recommandation "
        "d\u2019investissement, ni une sollicitation \u00e0 acheter ou "
        "vendre un instrument financier.",
        styles['warning']))
    story.append(safe_paragraph(
        "Les informations contenues dans ce livre sont g\u00e9n\u00e9rales "
        "et ne tiennent pas compte de votre situation financi\u00e8re "
        "personnelle, de vos objectifs ni de votre tol\u00e9rance au "
        "risque. Avant toute d\u00e9cision d\u2019investissement, consultez "
        "un conseiller financier agr\u00e9\u00e9 et ind\u00e9pendant.",
        styles['warning']))
    story.append(safe_paragraph(
        "L\u2019auteur et l\u2019\u00e9diteur d\u00e9clinent toute "
        "responsabilit\u00e9 quant aux pertes \u00e9ventuelles r\u00e9sultant "
        "de l\u2019utilisation des informations pr\u00e9sent\u00e9es dans "
        "cet ouvrage.",
        styles['warning']))
    story.append(PageBreak())

    # ── Table des matières ─────────────────────────────────────────────────
    story.append(NextPageTemplate('content'))
    story.append(RunningTitle("Table des mati\u00e8res"))

    toc_title_style = ParagraphStyle(
        'TOCTitle', fontName=FONT_SERIF_BOLD,
        fontSize=16, leading=21, alignment=TA_CENTER,
        spaceAfter=16, spaceBefore=6,
    )
    story.append(safe_paragraph("Table des mati\u00e8res", toc_title_style))

    toc = TableOfContents()
    toc.levelStyles = [styles['toc_level0'], styles['toc_level1']]
    toc.dotsMinLevel = 0
    toc.rightColumnWidth = 32
    story.append(toc)
    story.append(PageBreak())

    # ── Introduction ───────────────────────────────────────────────────────
    intro_path = os.path.join(chapitres_dir, "00_introduction.md")
    if os.path.isfile(intro_path):
        story.append(RunningTitle("Introduction"))
        story.append(safe_paragraph("Introduction", styles['sect_title']))
        md = read_file_safe(intro_path)
        if md:
            md = strip_chapter_heading(md, 0)
            flowables = parse_markdown(md, styles)
            story.extend(flowables)
        story.append(PageBreak())

    # ── Parties et chapitres (nombre variable, lu depuis plan.json) ────────
    for part_idx, partie in enumerate(parties, start=1):
        partie_titre = partie.get("titre", f"Partie {part_idx}")
        if partie_titre.lower().startswith("partie"):
            partie_label = partie_titre
        else:
            partie_label = f"Partie {part_idx} \u2013 {partie_titre}"

        # Page de partie (template blank, pas d'en-tête)
        story.append(NextPageTemplate('blank'))
        story.append(Spacer(1, 70 * mm))
        story.append(safe_paragraph(
            escape_xml(sanitize_winansi(partie_label)),
            styles['part_title']))
        story.append(PageBreak())
        story.append(NextPageTemplate('content'))

        chapitres = partie.get("chapitres", [])
        for chap in chapitres:
            chap_num = chap.get("numero", part_idx * 100 + chapitres.index(chap) + 1)
            chap_titre = chap.get("titre", f"Chapitre {chap_num}")
            chap_label = f"Chapitre {chap_num} \u2013 {chap_titre}"

            # Fichier du chapitre
            chap_file = os.path.join(chapitres_dir, f"ch{chap_num:02d}.md")
            if not os.path.isfile(chap_file):
                chap_file = os.path.join(chapitres_dir, f"ch{chap_num}.md")

            # Titre courant + titre visible
            story.append(RunningTitle(chap_label))
            story.append(safe_paragraph(
                escape_xml(sanitize_winansi(chap_label)),
                styles['chap_title']))
            story.append(Spacer(1, 4))

            if os.path.isfile(chap_file):
                md = read_file_safe(chap_file)
                if md:
                    md = strip_chapter_heading(md, chap_num)
                    flowables = parse_markdown(md, styles)
                    story.extend(flowables)
            else:
                story.append(safe_paragraph(
                    "<i>[Contenu du chapitre "
                    + str(chap_num) + " non trouv\u00e9.]</i>",
                    styles['body']))

            story.append(PageBreak())

    # ── Conclusion ─────────────────────────────────────────────────────────
    concl_path = os.path.join(chapitres_dir, "99_conclusion.md")
    if os.path.isfile(concl_path):
        story.append(RunningTitle("Conclusion"))
        story.append(safe_paragraph("Conclusion", styles['sect_title']))
        md = read_file_safe(concl_path)
        if md:
            md = strip_chapter_heading(md, 99)
            flowables = parse_markdown(md, styles)
            story.extend(flowables)
        story.append(PageBreak())

    # ── Bibliographie commentée ────────────────────────────────────────────
    story.append(RunningTitle("Bibliographie"))
    story.append(safe_paragraph("Bibliographie comment\u00e9e",
                                styles['sect_title']))
    story.append(Spacer(1, 6))

    for entry in bibliographie:
        auteur_ref = escape_xml(sanitize_winansi(entry.get("auteur", "")))
        titre_ref = escape_xml(sanitize_winansi(entry.get("titre", "")))
        annee_ref = escape_xml(str(entry.get("annee", "")))
        idee = escape_xml(sanitize_winansi(entry.get("idee", "")))
        apport = escape_xml(sanitize_winansi(entry.get("apport", "")))

        ref_line = f"<b>{auteur_ref}</b>, <i>{titre_ref}</i> ({annee_ref})."
        story.append(safe_paragraph(ref_line, styles['bib_entry']))
        if idee:
            story.append(safe_paragraph(
                f"<i>Id\u00e9e ma\u00eetresse :</i> {idee}",
                styles['bib_entry']))
        if apport:
            story.append(safe_paragraph(
                f"<i>Apport :</i> {apport}",
                styles['bib_entry']))
        story.append(Spacer(1, 4))

    # ── Build (multiBuild pour résoudre la TOC) ───────────────────────────
    doc.multiBuild(story)

    # ── Rapport final ──────────────────────────────────────────────────────
    try:
        from pypdf import PdfReader
        reader = PdfReader(output_path)
        n_pages = len(reader.pages)
    except Exception:
        n_pages = "?"
    print(f"\u2714 {output_path} g\u00e9n\u00e9r\u00e9 \u2014 {n_pages} pages.")
    return n_pages


def main():
    parser = argparse.ArgumentParser(
        description="Assemble le livre PDF depuis plan.json et le dossier "
                    "de chapitres Markdown.")
    parser.add_argument("plan", help="Chemin vers plan.json")
    parser.add_argument("chapitres", help="Dossier contenant les .md")
    parser.add_argument("output", help="Chemin du PDF de sortie")
    parser.add_argument("--corps", type=float, default=BODY_DEFAULT,
                        help=f"Taille du corps en pt (d\u00e9faut "
                             f"{BODY_DEFAULT})")
    parser.add_argument("--auteur", type=str,
                        default="Oussama Ghorbel",
                        help="Nom de l\u2019auteur "
                             "(d\u00e9faut : Oussama Ghorbel)")
    parser.add_argument("--annee", type=int, default=2026,
                        help="Ann\u00e9e de publication (d\u00e9faut : 2026)")
    parser.add_argument("--collection", type=str,
                        default="Biblioth\u00e8que Oussama Ghorbel",
                        help="Nom de la collection "
                             "(d\u00e9faut : Biblioth\u00e8que Oussama Ghorbel)")
    args = parser.parse_args()

    if not os.path.isfile(args.plan):
        print(f"Erreur : plan introuvable : {args.plan}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isdir(args.chapitres):
        print(f"Erreur : dossier chapitres introuvable : {args.chapitres}",
              file=sys.stderr)
        sys.exit(1)

    build_book(args.plan, args.chapitres, args.output, args.corps,
               args.auteur, args.annee, args.collection)

if __name__ == "__main__":
    main()
