
#!/usr/bin/env python3
"""
mise_en_page.py — Assemblage du livre PDF (Phase 3).
Usage : python3 mise_en_page.py plan.json chapitres livre.pdf [--corps 10.5]
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.colors import HexColor, black, white
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer,
    PageBreak, NextPageTemplate, KeepTogether, Table, TableStyle,
    HRFlowable, ListFlowable, ListItem, Preformatted
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

# ─── Constantes ───────────────────────────────────────────────────────────────

PAGE_W, PAGE_H = A5  # 148×210 mm en points
MARGIN = 18 * mm
BODY_DEFAULT = 10.5
LEADING_RATIO = 14.0 / 10.5  # ratio interligne/corps

DEJAVU_DIR = "/usr/share/fonts/truetype/dejavu"

# ─── Enregistrement des polices (robuste) ─────────────────────────────────────

def _try_register(name: str, path: str) -> bool:
    """Tente d'enregistrer une TTFont ; retourne True si succès."""
    if not os.path.isfile(path):
        return False
    try:
        pdfmetrics.registerFont(TTFont(name, path))
        return True
    except Exception:
        return False

def register_fonts() -> dict:
    """
    Enregistre les polices DejaVu disponibles.
    Retourne un dict avec les clés :
      serif, serif_bold, serif_italic, serif_bolditalic,
      sans, sans_bold, sans_italic, sans_bolditalic
    Chaque valeur est le nom de police utilisable dans reportlab.
    Ne plante JAMAIS sur une police manquante.
    """
    fm = {}

    # --- Serif ---
    if _try_register("Serif", os.path.join(DEJAVU_DIR, "DejaVuSerif.ttf")):
        fm['serif'] = "Serif"
    else:
        fm['serif'] = "Times-Roman"

    if _try_register("Serif-Bold", os.path.join(DEJAVU_DIR, "DejaVuSerif-Bold.ttf")):
        fm['serif_bold'] = "Serif-Bold"
    else:
        fm['serif_bold'] = "Times-Bold"

    # Italique serif
    serif_italic_registered = False
    for candidate_name, candidate_file in [
        ("Serif-Italic", "DejaVuSerif-Italic.ttf"),
        ("Serif-Italic", "DejaVuSans-Oblique.ttf"),
        ("Serif-Italic", "DejaVuSansMono-Oblique.ttf"),
    ]:
        if _try_register(candidate_name, os.path.join(DEJAVU_DIR, candidate_file)):
            fm['serif_italic'] = candidate_name
            serif_italic_registered = True
            break
    if not serif_italic_registered:
        fm['serif_italic'] = "Times-Italic"

    # BoldItalic serif
    serif_bi_registered = False
    for candidate_name, candidate_file in [
        ("Serif-BoldItalic", "DejaVuSerif-BoldItalic.ttf"),
        ("Serif-BoldItalic", "DejaVuSans-BoldOblique.ttf"),
        ("Serif-BoldItalic", "DejaVuSansMono-BoldOblique.ttf"),
    ]:
        if _try_register(candidate_name, os.path.join(DEJAVU_DIR, candidate_file)):
            fm['serif_bolditalic'] = candidate_name
            serif_bi_registered = True
            break
    if not serif_bi_registered:
        fm['serif_bolditalic'] = "Times-BoldItalic"

    # --- Sans ---
    if _try_register("Sans", os.path.join(DEJAVU_DIR, "DejaVuSans.ttf")):
        fm['sans'] = "Sans"
    else:
        fm['sans'] = "Helvetica"

    if _try_register("Sans-Bold", os.path.join(DEJAVU_DIR, "DejaVuSans-Bold.ttf")):
        fm['sans_bold'] = "Sans-Bold"
    else:
        fm['sans_bold'] = "Helvetica-Bold"

    # Italique sans
    sans_italic_registered = False
    for candidate_name, candidate_file in [
        ("Sans-Italic", "DejaVuSans-Oblique.ttf"),
        ("Sans-Italic", "DejaVuSansMono-Oblique.ttf"),
    ]:
        if _try_register(candidate_name, os.path.join(DEJAVU_DIR, candidate_file)):
            fm['sans_italic'] = candidate_name
            sans_italic_registered = True
            break
    if not sans_italic_registered:
        fm['sans_italic'] = "Helvetica-Oblique"

    # BoldItalic sans
    sans_bi_registered = False
    for candidate_name, candidate_file in [
        ("Sans-BoldItalic", "DejaVuSans-BoldOblique.ttf"),
        ("Sans-BoldItalic", "DejaVuSansMono-BoldOblique.ttf"),
    ]:
        if _try_register(candidate_name, os.path.join(DEJAVU_DIR, candidate_file)):
            fm['sans_bolditalic'] = candidate_name
            sans_bi_registered = True
            break
    if not sans_bi_registered:
        fm['sans_bolditalic'] = "Helvetica-BoldOblique"

    # --- Enregistrement des familles (tolérant aux erreurs) ---
    try:
        registerFontFamily(
            "Serif",
            normal=fm['serif'], bold=fm['serif_bold'],
            italic=fm['serif_italic'], boldItalic=fm['serif_bolditalic'],
        )
    except Exception:
        pass
    try:
        registerFontFamily(
            "Sans",
            normal=fm['sans'], bold=fm['sans_bold'],
            italic=fm['sans_italic'], boldItalic=fm['sans_bolditalic'],
        )
    except Exception:
        pass

    return fm

# ─── Conversion Markdown → Paragraph XML ──────────────────────────────────────

def escape_xml(text: str) -> str:
    """Échappe &, <, > pour le XML reportlab."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text

def _is_meaningful(content: str) -> bool:
    """
    Retourne True si le contenu entre marqueurs est significatif.
    Rejette : vide, uniquement des espaces, ou uniquement ponctuation/underscores/slashes.
    """
    stripped = content.strip()
    if not stripped:
        return False
    # Si le contenu n'est composé que de ponctuation, underscores, slashes, espaces → non significatif
    if re.match(r'^[\s_\-./\\,;:!?\'"()«»\[\]{}|~`^+=*#]+$', stripped):
        return False
    return True

def inline_markup(text: str) -> str:
    """
    Applique gras/italique sur du texte déjà échappé.
    Ordre : ***x*** / ___x___ → **x** / __x__ → *x* / _x_
    Ne convertit que si le contenu capturé est significatif.
    Les marqueurs orphelins restent littéraux.
    """

    def _sub_meaningful(pattern: str, tag_open: str, tag_close: str, txt: str) -> str:
        def replacer(m):
            content = m.group(1)
            if not _is_meaningful(content):
                return m.group(0)  # inchangé
            return tag_open + content + tag_close
        return re.sub(pattern, replacer, txt)

    # 1) Bold + Italic : ***x*** puis ___x___
    text = _sub_meaningful(r'\*\*\*(.+?)\*\*\*', '<b><i>', '</i></b>', text)
    text = _sub_meaningful(r'___(.+?)___', '<b><i>', '</i></b>', text)

    # 2) Bold : **x** puis __x__
    text = _sub_meaningful(r'\*\*(.+?)\*\*', '<b>', '</b>', text)
    text = _sub_meaningful(r'__(.+?)__', '<b>', '</b>', text)

    # 3) Italic : *x* puis _x_ (avec garde-fous word-boundary pour underscore)
    text = _sub_meaningful(r'\*(.+?)\*', '<i>', '</i>', text)
    text = _sub_meaningful(r'(?<!\w)_(.+?)_(?!\w)', '<i>', '</i>', text)

    return text

def md_to_xml(text: str) -> str:
    """Convertit une ligne Markdown en XML reportlab sûr."""
    text = escape_xml(text)
    text = inline_markup(text)
    return text

def strip_think_tags(text: str) -> str:
    """Supprime les blocs ."""
    return re.sub(r'', '', text, flags=re.DOTALL | re.IGNORECASE)

def safe_paragraph(text: str, style) -> Paragraph:
    """
    Crée un Paragraph. Si le XML est mal formé et lève une exception,
    retente avec le texte entièrement dépourvu de balises (échappé).
    Ne lève JAMAIS d'exception.
    """
    try:
        return Paragraph(text, style)
    except Exception:
        pass
    # Filet de sécurité : retirer toutes les balises XML
    try:
        plain = re.sub(r'<[^>]*>', '', text)
        return Paragraph(plain, style)
    except Exception:
        pass
    # Ultime recours : texte brut échappé
    try:
        raw = re.sub(r'<[^>]*>', '', text)
        raw = raw.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        raw = escape_xml(raw)
        return Paragraph(raw, style)
    except Exception:
        # Ne devrait jamais arriver, mais on retourne un paragraphe vide
        return Paragraph("", style)

def parse_markdown(md_text: str, styles: dict) -> list:
    """
    Convertit un texte Markdown en liste de flowables reportlab.
    Gère : #, ##, ###, ####, paragraphes, listes, citations, ---, tableaux.
    Ignore les blocs de code délimités par triple backtick.
    """
    flowables = []
    md_text = strip_think_tags(md_text)
    lines = md_text.split('\n')
    i = 0
    in_code_block = False
    in_list = False
    list_items = []
    list_type = None

    def flush_list():
        nonlocal in_list, list_items, list_type
        if list_items:
            for item_text in list_items:
                p = safe_paragraph(item_text, styles['list_item'])
                flowables.append(p)
            flowables.append(Spacer(1, 4))
        list_items = []
        in_list = False
        list_type = None

    while i < len(lines):
        line = lines[i]

        if line.strip().startswith('`' * 3):
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

        if re.match(r'^-{3,}$|^\*{3,}$|^_{3,}$', stripped):
            flush_list()
            flowables.append(Spacer(1, 6))
            flowables.append(HRFlowable(width="100%", thickness=0.5, color=HexColor("#999999")))
            flowables.append(Spacer(1, 6))
            i += 1
            continue

        heading_match = re.match(r'^(#{1,4})\s+(.*)', stripped)
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

        if stripped.startswith('>'):
            flush_list()
            quote_text = md_to_xml(stripped.lstrip('> '))
            flowables.append(safe_paragraph(quote_text, styles['quote']))
            i += 1
            continue

        if stripped.startswith('|') and stripped.endswith('|'):
            flush_list()
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                table_lines.append(lines[i].strip())
                i += 1
            flowables.extend(build_table(table_lines, styles))
            continue

        ul_match = re.match(r'^(\s*)[-*+]\s+(.*)', line)
        if ul_match:
            if not in_list or list_type != 'ul':
                flush_list()
                in_list = True
                list_type = 'ul'
            item_text = "• " + md_to_xml(ul_match.group(2))
            list_items.append(item_text)
            i += 1
            continue

        ol_match = re.match(r'^(\s*)\d+[.)]\s+(.*)', line)
        if ol_match:
            if not in_list or list_type != 'ol':
                flush_list()
                in_list = True
                list_type = 'ol'
            num = re.match(r'^(\s*)(\d+)[.)]\s+(.*)', line)
            item_text = f"{num.group(2)}. " + md_to_xml(num.group(3))
            list_items.append(item_text)
            i += 1
            continue

        flush_list()
        para_text = md_to_xml(stripped)
        if para_text.strip():
            flowables.append(safe_paragraph(para_text, styles['body']))
        i += 1

    flush_list()
    return flowables

def build_table(table_lines: list, styles: dict) -> list:
    """Construit un flowable Table à partir de lignes Markdown |."""
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
    table_data = [[safe_paragraph(c, styles['table_cell']) for c in row] for row in rows]
    col_w = (PAGE_W - 2 * MARGIN) / max(ncols, 1)
    t = Table(table_data, colWidths=[col_w] * ncols)
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), styles['body'].fontName),
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

def make_styles(fm: dict, corps: float) -> dict:
    """Construit les styles à partir du font_map."""
    leading = corps * LEADING_RATIO
    serif = fm['serif']
    serif_bold = fm['serif_bold']
    serif_italic = fm['serif_italic']
    serif_bolditalic = fm['serif_bolditalic']
    sans = fm['sans']
    sans_bold = fm['sans_bold']

    s = {}
    s['body'] = ParagraphStyle(
        'Body', fontName=serif, fontSize=corps, leading=leading,
        alignment=TA_JUSTIFY, spaceAfter=corps * 0.6, spaceBefore=0,
        firstLineIndent=corps * 1.5,
    )
    s['h1'] = ParagraphStyle(
        'H1', fontName=serif_bold,
        fontSize=corps * 1.8, leading=corps * 2.2,
        alignment=TA_LEFT, spaceBefore=corps * 1.5, spaceAfter=corps,
    )
    s['h2'] = ParagraphStyle(
        'H2', fontName=serif_bold,
        fontSize=corps * 1.45, leading=corps * 1.8,
        alignment=TA_LEFT, spaceBefore=corps * 1.2, spaceAfter=corps * 0.6,
    )
    s['h3'] = ParagraphStyle(
        'H3', fontName=serif_bold,
        fontSize=corps * 1.2, leading=corps * 1.5,
        alignment=TA_LEFT, spaceBefore=corps, spaceAfter=corps * 0.5,
    )
    s['h4'] = ParagraphStyle(
        'H4', fontName=serif_italic,
        fontSize=corps * 1.1, leading=corps * 1.4,
        alignment=TA_LEFT, spaceBefore=corps * 0.8, spaceAfter=corps * 0.4,
    )
    s['quote'] = ParagraphStyle(
        'Quote', fontName=serif_italic,
        fontSize=corps * 0.95, leading=leading * 0.95,
        alignment=TA_JUSTIFY, leftIndent=20, rightIndent=10,
        spaceBefore=corps * 0.5, spaceAfter=corps * 0.5,
        textColor=HexColor("#444444"),
    )
    s['list_item'] = ParagraphStyle(
        'ListItem', fontName=serif, fontSize=corps, leading=leading,
        alignment=TA_JUSTIFY, leftIndent=18, spaceAfter=corps * 0.3,
        bulletIndent=6,
    )
    s['title_page'] = ParagraphStyle(
        'TitlePage', fontName=serif_bold,
        fontSize=22, leading=28, alignment=TA_CENTER, spaceAfter=12,
    )
    s['subtitle'] = ParagraphStyle(
        'Subtitle', fontName=serif_italic,
        fontSize=14, leading=18, alignment=TA_CENTER, spaceAfter=8,
    )
    s['part_title'] = ParagraphStyle(
        'PartTitle', fontName=serif_bold,
        fontSize=18, leading=24, alignment=TA_CENTER, spaceBefore=80, spaceAfter=20,
    )
    s['toc_h1'] = ParagraphStyle(
        'TOCH1', fontName=serif_bold,
        fontSize=corps, leading=leading, spaceBefore=6, spaceAfter=2,
    )
    s['toc_h2'] = ParagraphStyle(
        'TOCH2', fontName=serif, fontSize=corps * 0.9, leading=leading * 0.9,
        leftIndent=12, spaceBefore=1, spaceAfter=1,
    )
    s['table_cell'] = ParagraphStyle(
        'TableCell', fontName=serif, fontSize=8.5, leading=11,
        alignment=TA_LEFT,
    )
    s['bib_entry'] = ParagraphStyle(
        'BibEntry', fontName=serif, fontSize=corps * 0.92, leading=leading * 0.92,
        alignment=TA_JUSTIFY, spaceAfter=corps * 0.8, leftIndent=12,
    )
    s['warning'] = ParagraphStyle(
        'Warning', fontName=serif, fontSize=corps, leading=leading,
        alignment=TA_JUSTIFY, spaceAfter=corps,
    )
    return s

# ─── Templates de page ────────────────────────────────────────────────────────

class BookDocTemplate(BaseDocTemplate):
    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)
        self._current_title = ""
        self._show_header = False
        self._font_map = {}

    def set_running_title(self, title):
        self._current_title = title

    def afterFlowable(self, flowable):
        """Notification pour la table des matières."""
        if isinstance(flowable, Paragraph):
            style_name = flowable.style.name
            if style_name == 'H1':
                text = flowable.getPlainText()
                self.notify('TOCEntry', (0, text, self.page))
            elif style_name == 'H2':
                text = flowable.getPlainText()
                self.notify('TOCEntry', (1, text, self.page))

def header_footer(canvas, doc):
    """Dessine en-tête (titre courant) et pied de page (numéro)."""
    canvas.saveState()
    page_num = canvas.getPageNumber()
    fm = doc._font_map

    # Pied de page : numéro
    footer_font = fm.get('serif', 'Times-Roman')
    canvas.setFont(footer_font, 8.5)
    canvas.drawCentredString(PAGE_W / 2, 10 * mm, str(page_num))

    # En-tête : titre courant (sauf premières pages)
    if page_num > 3 and doc._current_title:
        header_font = fm.get('serif_italic', 'Times-Italic')
        canvas.setFont(header_font, 8)
        canvas.drawCentredString(PAGE_W / 2, PAGE_H - 10 * mm, doc._current_title)
        canvas.setStrokeColor(HexColor("#AAAAAA"))
        canvas.setLineWidth(0.4)
        canvas.line(MARGIN, PAGE_H - 12 * mm, PAGE_W - MARGIN, PAGE_H - 12 * mm)
    canvas.restoreState()

def no_header_footer(canvas, doc):
    """Pages sans en-tête/pied (page de titre, etc.)."""
    pass

# ─── Construction du document ─────────────────────────────────────────────────

def build_book(plan_path: str, chapitres_dir: str, output_path: str, corps: float):
    fm = register_fonts()
    styles = make_styles(fm, corps)
    serif = fm['serif']

    # Charger le plan
    with open(plan_path, 'r', encoding='utf-8') as f:
        plan = json.load(f)

    titre = plan.get("titre", "Livre")
    sous_titre = plan.get("sous_titre", "")
    bibliographie = plan.get("bibliographie", [])
    parties = plan.get("parties", [])

    # Dimensions
    frame_w = PAGE_W - 2 * MARGIN
    frame_h = PAGE_H - 2 * MARGIN - 8 * mm

    # Frames
    frame_main = Frame(MARGIN, MARGIN + 5 * mm, frame_w, frame_h, id='main')
    frame_title = Frame(MARGIN, MARGIN, frame_w, PAGE_H - 2 * MARGIN, id='title')

    doc = BookDocTemplate(
        output_path,
        pagesize=A5,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN + 4 * mm, bottomMargin=MARGIN + 4 * mm,
        title=titre,
        author="Qwen 3.8 Max Thinking via Dialagram",
    )
    doc._font_map = fm

    # Templates
    tmpl_content = PageTemplate(id='content', frames=[frame_main], onPage=header_footer)
    tmpl_blank = PageTemplate(id='blank', frames=[frame_title], onPage=no_header_footer)
    doc.addPageTemplates([tmpl_blank, tmpl_content])

    story = []

    # ── Page de titre ──
    story.append(NextPageTemplate('blank'))
    story.append(Spacer(1, 50 * mm))
    story.append(safe_paragraph(escape_xml(titre), styles['title_page']))
    if sous_titre:
        story.append(Spacer(1, 6))
        story.append(safe_paragraph(escape_xml(sous_titre), styles['subtitle']))
    story.append(Spacer(1, 20 * mm))
    mention_style = ParagraphStyle(
        'Mention', fontName=serif, fontSize=9.5, leading=13,
        alignment=TA_CENTER, textColor=HexColor("#555555"),
    )
    story.append(safe_paragraph("Rédigé par Qwen 3.8 Max Thinking via Dialagram", mention_style))
    story.append(PageBreak())

    # ── Page d'avertissement ──
    story.append(Spacer(1, 20 * mm))
    warn_title_style = ParagraphStyle(
        'WarnTitle', fontName=fm['serif_bold'],
        fontSize=13, leading=17, alignment=TA_CENTER, spaceAfter=14,
    )
    story.append(safe_paragraph("Avertissement", warn_title_style))
    story.append(safe_paragraph(
        "Le présent ouvrage est fourni à titre strictement éducatif et informatif. "
        "Il ne constitue en aucun cas un conseil financier personnalisé, une recommandation "
        "d'investissement, ni une sollicitation à acheter ou vendre un instrument financier.",
        styles['warning']
    ))
    story.append(safe_paragraph(
        "Les informations contenues dans ce livre sont générales et ne tiennent pas compte "
        "de votre situation financière personnelle, de vos objectifs ni de votre tolérance "
        "au risque. Avant toute décision d'investissement, consultez un conseiller financier "
        "agréé et indépendant.",
        styles['warning']
    ))
    story.append(safe_paragraph(
        "L'auteur et l'éditeur déclinent toute responsabilité quant aux pertes éventuelles "
        "résultant de l'utilisation des informations présentées dans cet ouvrage.",
        styles['warning']
    ))
    story.append(PageBreak())

    # ── Table des matières ──
    story.append(NextPageTemplate('content'))
    doc.set_running_title(titre)

    toc_title_style = ParagraphStyle(
        'TOCTitle', fontName=fm['serif_bold'],
        fontSize=16, leading=20, alignment=TA_CENTER, spaceAfter=16, spaceBefore=10,
    )
    story.append(safe_paragraph("Table des matières", toc_title_style))

    toc = TableOfContents()
    toc.levelStyles = [styles['toc_h1'], styles['toc_h2']]
    toc.dotsMinLevel = 0
    story.append(toc)
    story.append(PageBreak())

    # ── Introduction ──
    intro_path = os.path.join(chapitres_dir, "00_introduction.md")
    if os.path.isfile(intro_path):
        doc.set_running_title("Introduction")
        md = read_file_safe(intro_path)
        if md:
            flowables = parse_markdown(md, styles)
            story.extend(flowables)
        story.append(PageBreak())

    # ── Parties et chapitres ──
    chap_counter = 1
    for partie in parties:
        partie_titre = partie.get("titre", "")
        story.append(NextPageTemplate('blank'))
        story.append(Spacer(1, 60 * mm))
        story.append(safe_paragraph(escape_xml(partie_titre), styles['part_title']))
        story.append(PageBreak())
        story.append(NextPageTemplate('content'))

        chapitres = partie.get("chapitres", [])
        for chap in chapitres:
            chap_num = chap.get("numero", chap_counter)
            chap_titre = chap.get("titre", f"Chapitre {chap_num}")

            chap_file = os.path.join(chapitres_dir, f"ch{chap_num:02d}.md")
            if not os.path.isfile(chap_file):
                chap_file = os.path.join(chapitres_dir, f"ch{chap_num}.md")

            doc.set_running_title(f"Chapitre {chap_num} – {chap_titre}")

            story.append(safe_paragraph(
                escape_xml(f"Chapitre {chap_num} – {chap_titre}"), styles['h1']
            ))
            story.append(Spacer(1, 4))

            if os.path.isfile(chap_file):
                md = read_file_safe(chap_file)
                if md:
                    md_clean = re.sub(r'^#\s+.*\n?', '', md, count=1)
                    flowables = parse_markdown(md_clean, styles)
                    story.extend(flowables)
            else:
                story.append(safe_paragraph(
                    f"<i>[Contenu du chapitre {chap_num} non trouvé.]</i>",
                    styles['body']
                ))

            story.append(PageBreak())
            chap_counter += 1

    # ── Conclusion ──
    concl_path = os.path.join(chapitres_dir, "99_conclusion.md")
    if os.path.isfile(concl_path):
        doc.set_running_title("Conclusion")
        md = read_file_safe(concl_path)
        if md:
            flowables = parse_markdown(md, styles)
            story.extend(flowables)
        story.append(PageBreak())

    # ── Bibliographie commentée ──
    doc.set_running_title("Bibliographie commentée")
    bib_title_style = ParagraphStyle(
        'BibTitle', fontName=fm['serif_bold'],
        fontSize=15, leading=20, alignment=TA_CENTER, spaceBefore=10, spaceAfter=14,
    )
    story.append(safe_paragraph("Bibliographie commentée", bib_title_style))
    story.append(Spacer(1, 6))

    for entry in bibliographie:
        auteur = escape_xml(entry.get("auteur", ""))
        titre_ref = escape_xml(entry.get("titre", ""))
        annee = escape_xml(str(entry.get("annee", "")))
        idee = escape_xml(entry.get("idee", ""))
        apport = escape_xml(entry.get("apport", ""))

        ref_line = f"<b>{auteur}</b>, <i>{titre_ref}</i> ({annee})."
        story.append(safe_paragraph(ref_line, styles['bib_entry']))
        if idee:
            story.append(safe_paragraph(f"<i>Idée maîtresse :</i> {idee}", styles['bib_entry']))
        if apport:
            story.append(safe_paragraph(f"<i>Apport :</i> {apport}", styles['bib_entry']))
        story.append(Spacer(1, 4))

    # ── Build avec multiBuild pour la TOC ──
    doc.multiBuild(story)

    # ── Rapport final ──
    try:
        from pypdf import PdfReader
        reader = PdfReader(output_path)
        n_pages = len(reader.pages)
    except Exception:
        n_pages = "?"
    print(f"✔ {output_path} généré — {n_pages} pages.")
    return n_pages

def read_file_safe(path: str) -> str:
    """Lit un fichier texte en ignorant les erreurs de décodage."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except Exception as e:
        print(f"⚠ Erreur lecture {path} : {e}", file=sys.stderr)
        return ""

# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Assemble le livre PDF à partir de plan.json et du dossier chapitres/."
    )
    parser.add_argument("plan", help="Chemin vers plan.json")
    parser.add_argument("chapitres", help="Dossier contenant les fichiers .md")
    parser.add_argument("output", help="Chemin du PDF de sortie")
    parser.add_argument("--corps", type=float, default=BODY_DEFAULT,
                        help=f"Taille du corps en pt (défaut {BODY_DEFAULT})")
    args = parser.parse_args()

    if not os.path.isfile(args.plan):
        print(f"Erreur : fichier plan introuvable : {args.plan}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isdir(args.chapitres):
        print(f"Erreur : dossier chapitres introuvable : {args.chapitres}", file=sys.stderr)
        sys.exit(1)

    build_book(args.plan, args.chapitres, args.output, args.corps)

if __name__ == "__main__":
    main()
