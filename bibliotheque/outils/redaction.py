
#!/usr/bin/env python3
"""redaction.py – Génération parallèle des chapitres d'un livre via API LLM (streaming SSE).

Usage :
    python3 redaction.py plan.json dossier_sortie [--auteur "Oussama Ghorbel"] [--mots 3500] [--parallele 8]
"""

import argparse
import json
import sys
import time
import re
import urllib.request
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

API_URL = "https://dialagram.me/router/v1/chat/completions"
MODEL = "qwen-3.8-max-thinking"
TIMEOUT = 1500
MAX_RETRIES = 5
MAX_CONTINUATIONS = 6
MIN_WORDS_INTRO = 2700
MIN_WORDS_CONCL = 2200

# Phrases interdites (chatbot) – insensible à la casse
FORBIDDEN_PATTERNS = [
    r"dites[\s-]?moi",
    r"souhaitez[\s-]?vous\s+que\s+je",
    r"voulez[\s-]?vous\s+que\s+je",
    r"si\s+vous\s+souhaitez\s+que\s+j",
    r"je\s+peux\s*:",
    r"ce\s+que\s+je\s+peux\s+faire",
    r"je\s+m['\u2019]y\s+mets",
    r"passer\s+au\s+chapitre",
    r"passer\s+directement\s+au\s+chapitre",
    r"ma\s+r[ée]ponse\s+pr[ée]c[ée]dente",
    r"int[ée]gralement\s+termin[ée]",
    r"int[ée]gralement\s+r[ée]dig[ée]",
    r"je\s+r[ée]dige\s+imm[ée]diatement",
]
FORBIDDEN_RE = [re.compile(p, re.IGNORECASE) for p in FORBIDDEN_PATTERNS]

# Phrases indiquant que le modèle considère le texte comme terminé
DONE_PATTERNS = [
    r"chapitre\s+\d+\s+(?:est\s+)?(?:int[ée]gralement\s+)?(?:termin[ée]|r[ée]dig[ée]|complet)",
    r"le\s+chapitre\s+est\s+(?:termin[ée]|complet|r[ée]dig[ée])",
    r"souhaitez[\s-]?vous\s+que\s+je\s+(?:r[ée]dige|passe)",
    r"dites[\s-]?moi\s+si",
    r"n['\u2019]h[ée]sitez\s+pas",
]
DONE_RE = [re.compile(p, re.IGNORECASE) for p in DONE_PATTERNS]

# Appel API streaming SSE avec capture usage

def call_api(messages: list[dict]) -> tuple[str, dict]:
    """Envoie une requête streaming et retourne (texte complet, dict usage)."""
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": True,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    chunks: list[str] = []
    usage: dict = {}

    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        buffer = ""
        while True:
            raw = resp.read(4096)
            if not raw:
                break
            buffer += raw.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip().replace("\r", "")
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    return "".join(chunks), usage
                try:
                    obj = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                # Capturer usage si présent (souvent dans le dernier chunk)
                if "usage" in obj and obj["usage"]:
                    usage = obj["usage"]
                choices = obj.get("choices")
                if not choices:
                    continue
                delta = choices[0].get("delta")
                if not delta:
                    continue
                content = delta.get("content")
                if content:
                    chunks.append(content)
    return "".join(chunks), usage

def call_with_retry(messages: list[dict]) -> tuple[str, dict, int]:
    """Appel API avec backoff exponentiel. Retourne (texte, usage, tentatives)."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result, usage = call_api(messages)
            if result.strip():
                return result, usage, attempt
            raise ValueError("Réponse vide du modèle")
        except Exception as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                wait = 15 * 2 ** attempt
                print(f"    ⚠ tentative {attempt}/{MAX_RETRIES} échouée ({exc}). Retry dans {wait}s…")
                time.sleep(wait)
    raise RuntimeError(f"Échec après {MAX_RETRIES} tentatives : {last_exc}") from last_exc

# Filtrage anti-chatbot

def is_chatbot_paragraph(paragraph: str) -> bool:
    """Vérifie si un paragraphe contient une phrase interdite."""
    for rx in FORBIDDEN_RE:
        if rx.search(paragraph):
            return True
    return False

def looks_like_done_message(text: str) -> bool:
    """Détecte si la réponse est un message de type 'chapitre terminé' plutôt que du contenu."""
    for rx in DONE_RE:
        if rx.search(text):
            return True
    return False

def filter_chatbot(text: str) -> str:
    """Supprime les paragraphes contenant des phrases chatbot."""
    paragraphs = text.split("\n\n")
    kept = []
    for para in paragraphs:
        if not is_chatbot_paragraph(para):
            kept.append(para)
    return "\n\n".join(kept)

def remove_duplicated_tail(text: str) -> str:
    """Détecte et retire un bloc final dupliqué (les 2-3 derniers paragraphes
    identiques à un bloc situé plus tôt dans le texte)."""
    paragraphs = text.split("\n\n")
    if len(paragraphs) < 6:
        return text

    # Vérifier si les 2 ou 3 derniers paragraphes apparaissent aussi plus tôt
    for tail_len in (3, 2):
        tail = paragraphs[-tail_len:]
        tail_joined = "\n\n".join(tail).strip()
        if not tail_joined:
            continue
        # Chercher cette séquence dans le reste du texte
        body = "\n\n".join(paragraphs[:-tail_len])
        if tail_joined in body:
            paragraphs = paragraphs[:-tail_len]
            break

    return "\n\n".join(paragraphs)

def clean_response(text: str) -> str:
    """Pipeline complet de nettoyage d'une réponse."""
    text = filter_chatbot(text)
    text = remove_duplicated_tail(text)
    return text.strip()

# Utilitaires

def count_words(text: str) -> int:
    return len(text.split())

def build_system_prompt(
    book_title: str,
    book_subtitle: str,
    plan_summary: str,
    target_words: int,
    author: str,
) -> str:
    return (
        f"Tu es {author}, auteur du livre « {book_title} »"
        + (f" (sous-titre : {book_subtitle})" if book_subtitle else "")
        + ".\n\n"
        f"Quand c'est naturel, tu écris à la première personne en tant qu'{author}. "
        f"Tu n'inventes jamais de biographie précise ni d'anecdotes personnelles vérifiables.\n\n"
        f"Plan global du livre :\n{plan_summary}\n\n"
        f"Règles de rédaction :\n"
        f"- Contenu 100 % original. Aucune reproduction de textes protégés par le droit d'auteur.\n"
        f"- Citations courtes uniquement, toujours attribuées à leur auteur/source.\n"
        f"- Aucune promesse irréaliste (rendements garantis, succès assuré, etc.).\n"
        f"- Si le sujet touche à la finance ou à l'investissement, inclure un avertissement : "
        f"ce contenu est informatif et ne constitue pas un conseil financier personnalisé.\n"
        f"- Rédaction en français, style clair, pédagogique et engageant.\n"
        f"- Format Markdown : ## pour le titre principal du chapitre, ### pour chaque section.\n"
        f"- Longueur cible : environ {target_words} mots.\n\n"
        f"CONSIGNE ABSOLUE : Ta réponse contient EXCLUSIVEMENT le texte du livre. "
        f"Jamais de message au commanditaire, jamais de question, jamais de proposition "
        f"de continuer ou de passer à autre chose. Uniquement la prose du livre."
    )

def build_plan_summary(plan: dict) -> str:
    lines: list[str] = []
    for partie in plan.get("parties", []):
        lines.append(f"Partie – {partie.get('titre', '')}")
        for chap in partie.get("chapitres", []):
            lines.append(f"  Chapitre {chap.get('numero', '?')} – {chap.get('titre', '')}")
    return "\n".join(lines)

def extract_chapters(plan: dict) -> list[dict]:
    chapters: list[dict] = []
    for partie in plan.get("parties", []):
        for chap in partie.get("chapitres", []):
            chapters.append(chap)
    return chapters

# Génération avec continuation

CONTINUATION_INSTRUCTION = (
    "Continue exactement là où tu t'es arrêté, sans répéter ce qui précède, "
    "jusqu'à la fin du chapitre. Ta réponse contient EXCLUSIVEMENT la suite du texte "
    "du livre. Aucun message, aucune question, aucune proposition. Uniquement la prose."
)

def generate_with_continuation(
    system_prompt: str,
    user_prompt: str,
    min_words: int,
) -> tuple[str, dict, int]:
    """Génère un texte avec continuations. Retourne (texte, usage_cumulé, tentatives_totales)."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    raw_text, usage, attempts = call_with_retry(messages)
    total_usage = dict(usage) if usage else {}
    total_attempts = attempts

    text = clean_response(raw_text)

    continuations = 0
    while count_words(text) < min_words and continuations < MAX_CONTINUATIONS:
        continuations += 1
        cont_messages = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": CONTINUATION_INSTRUCTION},
        ]
        extra_raw, extra_usage, extra_attempts = call_with_retry(cont_messages)
        total_attempts += extra_attempts
        # Cumuler les tokens
        if extra_usage:
            for k, v in extra_usage.items():
                if isinstance(v, (int, float)):
                    total_usage[k] = total_usage.get(k, 0) + v

        # (b) Si le modèle répond que c'est terminé, on arrête
        if looks_like_done_message(extra_raw):
            break

        extra_clean = clean_response(extra_raw)
        if not extra_clean.strip():
            break

        text += "\n\n" + extra_clean

    # Nettoyage final
    text = clean_response(text)
    return text, total_usage, total_attempts

def generate_chapter(
    chap: dict,
    plan_summary: str,
    book_title: str,
    book_subtitle: str,
    author: str,
    target_words: int,
) -> tuple[str, dict, int]:
    ch_num = chap.get("numero", "?")
    ch_title = chap.get("titre", "")
    ch_objective = chap.get("objectif", "")
    sections = chap.get("sections", [])
    exercises = chap.get("exercices", [])

    sections_text = "\n".join(f"  - {s}" for s in sections) if sections else "  (aucune section précisée)"
    exercises_text = "\n".join(f"  - {e}" for e in exercises) if exercises else "  (aucun exercice précisé)"

    user_prompt = (
        f"Rédige le chapitre {ch_num} : « {ch_title} ».\n\n"
        f"Objectif : {ch_objective}\n\n"
        f"Sections à développer :\n{sections_text}\n\n"
        f"Exercices à proposer en fin de chapitre :\n{exercises_text}\n\n"
        f"Rédige maintenant le chapitre complet (~{target_words} mots) en Markdown. "
        f"Ta réponse contient uniquement le texte du chapitre, rien d'autre."
    )

    sys_prompt = build_system_prompt(book_title, book_subtitle, plan_summary, target_words, author)
    return generate_with_continuation(sys_prompt, user_prompt, target_words - 300)

def generate_intro(
    book_title: str,
    book_subtitle: str,
    plan_summary: str,
    author: str,
    target_words: int,
) -> tuple[str, dict, int]:
    sys_prompt = build_system_prompt(book_title, book_subtitle, plan_summary, target_words, author)
    user_prompt = (
        f"Rédige l'introduction complète du livre (~{target_words} mots) en Markdown.\n"
        "Présente le sujet, la démarche, le public visé et annonce le plan.\n"
        "Ta réponse contient uniquement le texte de l'introduction, rien d'autre."
    )
    return generate_with_continuation(sys_prompt, user_prompt, MIN_WORDS_INTRO)

def generate_conclusion(
    book_title: str,
    book_subtitle: str,
    plan_summary: str,
    author: str,
    target_words: int,
) -> tuple[str, dict, int]:
    sys_prompt = build_system_prompt(book_title, book_subtitle, plan_summary, target_words, author)
    user_prompt = (
        f"Rédige la conclusion complète du livre (~{target_words} mots) en Markdown.\n"
        "Synthétise les apports principaux, ouvre des perspectives et adresse un mot final au lecteur.\n"
        "Ta réponse contient uniquement le texte de la conclusion, rien d'autre."
    )
    return generate_with_continuation(sys_prompt, user_prompt, MIN_WORDS_CONCL)

# Worker chapitre

def process_chapter(
    chap: dict,
    plan_summary: str,
    book_title: str,
    book_subtitle: str,
    author: str,
    target_words: int,
    out_dir: Path,
):
    ch_num = int(chap.get("numero", 0))
    filename = f"ch{ch_num:02d}.md"
    filepath = out_dir / filename

    # Reprise
    if filepath.exists():
        existing = filepath.read_text(encoding="utf-8")
        if count_words(existing) >= target_words - 300:
            print(f"  [skip] Chapitre {ch_num:02d} – déjà présent ({count_words(existing)} mots)")
            return {
                "numero": ch_num,
                "fichier": filename,
                "mots": count_words(existing),
                "duree_s": 0.0,
                "tentatives": 0,
                "usage": {},
                "skipped": True,
            }

    t0 = time.time()
    print(f"  [start] Chapitre {ch_num:02d} – {chap.get('titre', '')}")

    text, usage, attempts = generate_chapter(
        chap, plan_summary, book_title, book_subtitle, author, target_words
    )
    filepath.write_text(text, encoding="utf-8")

    elapsed = time.time() - t0
    wc = count_words(text)
    print(f"  [done]  Chapitre {ch_num:02d} – {wc} mots – {elapsed:.1f}s – {attempts} appel(s)")
    return {
        "numero": ch_num,
        "fichier": filename,
        "mots": wc,
        "duree_s": round(elapsed, 1),
        "tentatives": attempts,
        "usage": usage,
        "skipped": False,
    }

# Rapport

def write_report(out_dir: Path, entries: list[dict], global_elapsed: float) -> None:
    total_words = sum(e["mots"] for e in entries)
    total_tokens_prompt = sum(e.get("usage", {}).get("prompt_tokens", 0) for e in entries)
    total_tokens_completion = sum(e.get("usage", {}).get("completion_tokens", 0) for e in entries)
    total_tokens = sum(e.get("usage", {}).get("total_tokens", 0) for e in entries)
    total_attempts = sum(e["tentatives"] for e in entries)

    report = {
        "resume": {
            "fichiers": len(entries),
            "mots_total": total_words,
            "duree_totale_s": round(global_elapsed, 1),
            "tentatives_totales": total_attempts,
            "tokens": {
                "prompt": total_tokens_prompt,
                "completion": total_tokens_completion,
                "total": total_tokens,
            },
        },
        "chapitres": entries,
    }
    report_path = out_dir / "rapport_redaction.json"
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\n📄 Rapport écrit : {report_path}")

# Main

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Génération parallèle des chapitres d'un livre via API LLM."
    )
    parser.add_argument("plan", help="Chemin vers plan.json")
    parser.add_argument("dossier_sortie", help="Répertoire de sortie")
    parser.add_argument("--auteur", default="Oussama Ghorbel", help="Nom de l'auteur")
    parser.add_argument("--mots", type=int, default=3500, help="Mots cibles par chapitre")
    parser.add_argument("--parallele", type=int, default=8, help="Nombre de workers parallèles")
    args = parser.parse_args()

    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"Erreur : fichier « {plan_path} » introuvable.")
        sys.exit(1)

    with open(plan_path, "r", encoding="utf-8") as fh:
        plan = json.load(fh)

    book_title = plan.get("titre", "Sans titre")
    book_subtitle = plan.get("sous_titre", "")
    author = args.auteur
    target_words = args.mots
    parallel = args.parallele
    out_dir = Path(args.dossier_sortie)
    out_dir.mkdir(parents=True, exist_ok=True)

    plan_summary = build_plan_summary(plan)
    chapters = extract_chapters(plan)

    print(f"📘 Livre : {book_title}")
    print(f"   Sous-titre : {book_subtitle or '(aucun)'}")
    print(f"   Auteur : {author}")
    print(f"   Chapitres à rédiger : {len(chapters)}")
    print(f"   Mots cibles / chapitre : {target_words}")
    print(f"   Répertoire de sortie : {out_dir.resolve()}")
    print(f"   Parallélisme : {parallel}\n")

    global_start = time.time()
    report_entries: list[dict] = []

    # ── Introduction ──
    intro_path = out_dir / "00_introduction.md"
    if intro_path.exists() and count_words(intro_path.read_text(encoding="utf-8")) >= MIN_WORDS_INTRO:
        print("  [skip] Introduction – déjà présente")
        report_entries.append({
            "numero": 0,
            "fichier": "00_introduction.md",
            "mots": count_words(intro_path.read_text(encoding="utf-8")),
            "duree_s": 0.0,
            "tentatives": 0,
            "usage": {},
            "skipped": True,
        })
    else:
        print("  [start] Introduction")
        t0 = time.time()
        intro_text, intro_usage, intro_attempts = generate_intro(
            book_title, book_subtitle, plan_summary, author, 3000
        )
        intro_path.write_text(intro_text, encoding="utf-8")
        elapsed = time.time() - t0
        wc = count_words(intro_text)
        print(f"  [done]  Introduction – {wc} mots – {elapsed:.1f}s")
        report_entries.append({
            "numero": 0,
            "fichier": "00_introduction.md",
            "mots": wc,
            "duree_s": round(elapsed, 1),
            "tentatives": intro_attempts,
            "usage": intro_usage,
            "skipped": False,
        })

    # ── Chapitres en parallèle ──
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {
            pool.submit(
                process_chapter,
                chap,
                plan_summary,
                book_title,
                book_subtitle,
                author,
                target_words,
                out_dir,
            ): chap
            for chap in chapters
        }
        for fut in as_completed(futures):
            chap = futures[fut]
            try:
                entry = fut.result()
                report_entries.append(entry)
            except Exception as exc:
                ch_num = chap.get("numero", "?")
                print(f"  [ERREUR] Chapitre {ch_num} : {exc}")
                report_entries.append({
                    "numero": int(ch_num) if str(ch_num).isdigit() else -1,
                    "fichier": f"ch{int(ch_num):02d}.md" if str(ch_num).isdigit() else "?",
                    "mots": 0,
                    "duree_s": 0.0,
                    "tentatives": 0,
                    "usage": {},
                    "skipped": False,
                    "erreur": str(exc),
                })

    # ── Conclusion ──
    concl_path = out_dir / "99_conclusion.md"
    if concl_path.exists() and count_words(concl_path.read_text(encoding="utf-8")) >= MIN_WORDS_CONCL:
        print("  [skip] Conclusion – déjà présente")
        report_entries.append({
            "numero": 99,
            "fichier": "99_conclusion.md",
            "mots": count_words(concl_path.read_text(encoding="utf-8")),
            "duree_s": 0.0,
            "tentatives": 0,
            "usage": {},
            "skipped": True,
        })
    else:
        print("  [start] Conclusion")
        t0 = time.time()
        concl_text, concl_usage, concl_attempts = generate_conclusion(
            book_title, book_subtitle, plan_summary, author, 2500
        )
        concl_path.write_text(concl_text, encoding="utf-8")
        elapsed = time.time() - t0
        wc = count_words(concl_text)
        print(f"  [done]  Conclusion – {wc} mots – {elapsed:.1f}s")
        report_entries.append({
            "numero": 99,
            "fichier": "99_conclusion.md",
            "mots": wc,
            "duree_s": round(elapsed, 1),
            "tentatives": concl_attempts,
            "usage": concl_usage,
            "skipped": False,
        })

    # ── Trier les entrées par numéro ──
    report_entries.sort(key=lambda e: e.get("numero", 0))

    # ── Rapport ──
    global_elapsed = time.time() - global_start
    write_report(out_dir, report_entries, global_elapsed)

    # ── Résumé final ──
    total_words = sum(e["mots"] for e in report_entries)
    skipped_count = sum(1 for e in report_entries if e.get("skipped"))
    generated_count = len(report_entries) - skipped_count
    total_tokens = sum(e.get("usage", {}).get("total_tokens", 0) for e in report_entries)
    total_attempts = sum(e["tentatives"] for e in report_entries)

    print(f"\n{'═' * 60}")
    print("RÉSUMÉ FINAL")
    print(f"{'═' * 60}")
    print(f"  Auteur             : {author}")
    print(f"  Fichiers générés   : {generated_count}")
    print(f"  Fichiers sautés    : {skipped_count}")
    print(f"  Mots totaux        : {total_words}")
    print(f"  Tokens totaux      : {total_tokens}")
    print(f"  Appels API totaux  : {total_attempts}")
    print(f"  Temps total        : {global_elapsed:.1f}s ({global_elapsed / 60:.1f} min)")
    print(f"  Fichiers           : {out_dir.resolve()}")
    print(f"{'═' * 60}")

if __name__ == "__main__":
    main()
