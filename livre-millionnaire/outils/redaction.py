
#!/usr/bin/env python3
"""redaction.py – Génération parallèle des chapitres d'un livre via API LLM (streaming SSE)."""

import json
import sys
import time
import re
import urllib.request
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

API_URL = "https://dialagram.me/router/v1/chat/completions"
MODEL = "qwen-3.8-max-thinking"
TIMEOUT = 1500
MAX_RETRIES = 5
MAX_CONTINUATIONS = 3
MIN_WORDS_CHAPTER = 3200
MIN_WORDS_INTRO = 2700
MIN_WORDS_CONCL = 2200
PARALLEL = 8

# Appel API streaming SSE

def call_api(messages: list[dict]) -> str:
    """Envoie une requête streaming et retourne le texte complet."""
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
                    return "".join(chunks)
                try:
                    obj = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices")
                if not choices:
                    continue
                delta = choices[0].get("delta")
                if not delta:
                    continue
                content = delta.get("content")
                if content:
                    chunks.append(content)
    return "".join(chunks)

def call_with_retry(messages: list[dict]) -> str:
    """Appel API avec backoff exponentiel (5 tentatives)."""
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = call_api(messages)
            if result.strip():
                return result
            raise ValueError("Réponse vide du modèle")
        except Exception as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt * 2
                print(f"    ⚠ tentative {attempt}/{MAX_RETRIES} échouée ({exc}). Retry dans {wait}s…")
                time.sleep(wait)
    raise RuntimeError(f"Échec après {MAX_RETRIES} tentatives : {last_exc}") from last_exc

# Utilitaires

def count_words(text: str) -> int:
    return len(text.split())

def build_system_prompt(book_title: str, book_subtitle: str, plan_summary: str, target_words: int) -> str:
    return (
        f"Tu es l'auteur du livre « {book_title} » (sous-titre : {book_subtitle}).\n\n"
        f"Plan global du livre :\n{plan_summary}\n\n"
        f"Règles de rédaction :\n"
        f"- Contenu 100 % original. Aucune reproduction de textes protégés par le droit d'auteur.\n"
        f"- Citations courtes uniquement, toujours attribuées à leur auteur/source.\n"
        f"- Aucune promesse irréaliste (rendements garantis, succès assuré, etc.).\n"
        f"- Si le sujet touche à la finance ou à l'investissement, inclure un avertissement : "
        f"ce contenu est informatif et ne constitue pas un conseil financier personnalisé.\n"
        f"- Rédaction en français, style clair, pédagogique et engageant.\n"
        f"- Format Markdown : ## pour le titre principal du chapitre, ### pour chaque section.\n"
        f"- Longueur cible : environ {target_words} mots."
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

def generate_with_continuation(
    system_prompt: str,
    user_prompt: str,
    min_words: int,
    continuation_msg: str,
) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    text = call_with_retry(messages)

    continuations = 0
    while count_words(text) < min_words and continuations < MAX_CONTINUATIONS:
        continuations += 1
        cont_messages = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": continuation_msg},
        ]
        extra = call_with_retry(cont_messages)
        if not extra.strip():
            break
        text += "\n" + extra

    return text

def generate_chapter(chap: dict, plan_summary: str, book_title: str, book_subtitle: str) -> str:
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
        f"Rédige maintenant le chapitre complet (~3 500 mots) en Markdown."
    )

    sys_prompt = build_system_prompt(book_title, book_subtitle, plan_summary, 3500)
    return generate_with_continuation(
        sys_prompt,
        user_prompt,
        MIN_WORDS_CHAPTER,
        "Continue exactement là où tu t'es arrêté, sans répéter, jusqu'à la fin du chapitre.",
    )

def generate_intro(book_title: str, book_subtitle: str, plan_summary: str) -> str:
    sys_prompt = build_system_prompt(book_title, book_subtitle, plan_summary, 3000)
    user_prompt = (
        "Rédige l'introduction complète du livre (~3 000 mots) en Markdown.\n"
        "Présente le sujet, la démarche, le public visé et annonce le plan."
    )
    return generate_with_continuation(
        sys_prompt,
        user_prompt,
        MIN_WORDS_INTRO,
        "Continue exactement là où tu t'es arrêté, sans répéter, jusqu'à la fin de l'introduction.",
    )

def generate_conclusion(book_title: str, book_subtitle: str, plan_summary: str) -> str:
    sys_prompt = build_system_prompt(book_title, book_subtitle, plan_summary, 2500)
    user_prompt = (
        "Rédige la conclusion complète du livre (~2 500 mots) en Markdown.\n"
        "Synthétise les apports principaux, ouvre des perspectives et adresse un mot final au lecteur."
    )
    return generate_with_continuation(
        sys_prompt,
        user_prompt,
        MIN_WORDS_CONCL,
        "Continue exactement là où tu t'es arrêté, sans répéter, jusqu'à la fin de la conclusion.",
    )

# Worker chapitre

def process_chapter(chap: dict, plan_summary: str, book_title: str, book_subtitle: str, out_dir: Path):
    ch_num = int(chap.get("numero", 0))
    filename = f"ch{ch_num:02d}.md"
    filepath = out_dir / filename

    # Reprise
    if filepath.exists():
        existing = filepath.read_text(encoding="utf-8")
        if count_words(existing) >= MIN_WORDS_CHAPTER:
            print(f"  [skip] Chapitre {ch_num:02d} – déjà présent ({count_words(existing)} mots)")
            return ch_num, count_words(existing), 0.0, True

    t0 = time.time()
    print(f"  [start] Chapitre {ch_num:02d} – {chap.get('titre', '')}")

    text = generate_chapter(chap, plan_summary, book_title, book_subtitle)
    filepath.write_text(text, encoding="utf-8")

    elapsed = time.time() - t0
    wc = count_words(text)
    print(f"  [done]  Chapitre {ch_num:02d} – {wc} mots – {elapsed:.1f}s")
    return ch_num, wc, elapsed, False

# Main

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage : python3 redaction.py plan.json")
        sys.exit(1)

    plan_path = Path(sys.argv[1])
    if not plan_path.exists():
        print(f"Erreur : fichier « {plan_path} » introuvable.")
        sys.exit(1)

    with open(plan_path, "r", encoding="utf-8") as fh:
        plan = json.load(fh)

    book_title = plan.get("titre", "Sans titre")
    book_subtitle = plan.get("sous_titre", "")
    plan_summary = build_plan_summary(plan)
    chapters = extract_chapters(plan)

    out_dir = Path("chapitres")
    out_dir.mkdir(exist_ok=True)

    print(f"📘 Livre : {book_title}")
    print(f"   Sous-titre : {book_subtitle}")
    print(f"   Chapitres à rédiger : {len(chapters)}")
    print(f"   Répertoire de sortie : {out_dir.resolve()}")
    print(f"   Parallélisme : {PARALLEL}\n")

    global_start = time.time()
    results: list[tuple[int, int, float, bool]] = []

    # ── Introduction ──
    intro_path = out_dir / "00_introduction.md"
    if intro_path.exists() and count_words(intro_path.read_text(encoding="utf-8")) >= MIN_WORDS_INTRO:
        print("  [skip] Introduction – déjà présente")
    else:
        print("  [start] Introduction")
        t0 = time.time()
        intro_text = generate_intro(book_title, book_subtitle, plan_summary)
        intro_path.write_text(intro_text, encoding="utf-8")
        print(f"  [done]  Introduction – {count_words(intro_text)} mots – {time.time()-t0:.1f}s")

    # ── Chapitres (8 en parallèle) ──
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        futures = {
            pool.submit(process_chapter, chap, plan_summary, book_title, book_subtitle, out_dir): chap
            for chap in chapters
        }
        for fut in as_completed(futures):
            chap = futures[fut]
            try:
                results.append(fut.result())
            except Exception as exc:
                print(f"  [ERREUR] Chapitre {chap.get('numero', '?')} : {exc}")

    # ── Conclusion ──
    concl_path = out_dir / "99_conclusion.md"
    if concl_path.exists() and count_words(concl_path.read_text(encoding="utf-8")) >= MIN_WORDS_CONCL:
        print("  [skip] Conclusion – déjà présente")
    else:
        print("  [start] Conclusion")
        t0 = time.time()
        concl_text = generate_conclusion(book_title, book_subtitle, plan_summary)
        concl_path.write_text(concl_text, encoding="utf-8")
        print(f"  [done]  Conclusion – {count_words(concl_text)} mots – {time.time()-t0:.1f}s")

    # ── Résumé final ──
    total_elapsed = time.time() - global_start
    total_words = sum(wc for _, wc, _, _ in results)
    skipped = sum(1 for _, _, _, s in results if s)
    generated = len(results) - skipped

    print(f"\n{'═' * 60}")
    print("RÉSUMÉ FINAL")
    print(f"{'═' * 60}")
    print(f"  Chapitres générés  : {generated}")
    print(f"  Chapitres sautés   : {skipped}")
    print(f"  Mots (chapitres)   : {total_words}")
    print(f"  Temps total        : {total_elapsed:.1f}s ({total_elapsed / 60:.1f} min)")
    print(f"  Fichiers           : {out_dir.resolve()}")
    print(f"{'═' * 60}")

if __name__ == "__main__":
    main()
