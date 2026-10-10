
#!/usr/bin/env python3
"""nettoyage.py – Correction éditoriale automatisée des chapitres Markdown.

Usage : python3 nettoyage.py chapitres chapitres_v2
"""

import json
import re
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
API_URL = "https://dialagram.me/router/v1/chat/completions"
MODEL = "qwen-3.8-max-thinking"
TIMEOUT_S = 1500
MAX_ATTEMPTS = 5
PARALLEL = 8
WORD_RATIO_MIN = 0.70

BOT_PATTERNS = [
    r"dites-moi",
    r"si vous souhaitez que j",
    r"je peux :",
    r"ce que je peux faire",
    r"voulez-vous que je",
    r"souhaitez-vous que je",
    r"je m'y mets",
    r"passer au chapitre",
    r"passer directement au chapitre",
]

BOT_PATTERNS_RE = [re.compile(p, re.IGNORECASE) for p in BOT_PATTERNS]

EDITOR_SYSTEM = (
    "Tu es un éditeur professionnel. Tu reçois le texte Markdown d'un chapitre de livre. "
    "Applique STRICTEMENT les corrections suivantes, sans rien changer d'autre :\n\n"
    "1. SUPPRIME toute phrase ou tout bloc où l'auteur/IA s'adresse à un commanditaire : "
    "propositions de continuation (« Si vous souhaitez que j'enchaîne », « Ce que je peux faire maintenant », "
    "« Dites-moi simplement comment vous voulez procéder », « je peux rédiger », listes numérotées de prochaines étapes, etc.). "
    "Supprime aussi les passages dupliqués par les requêtes de continuation (paragraphes répétés en fin de chapitre).\n\n"
    "2. AVERTISSEMENT FINANCIER : si le chapitre commence par un long avertissement/disclaimer financier générique "
    "(type « Ce contenu est fourni à titre informatif… ne constitue pas un conseil en investissement… »), "
    "supprime-le entièrement. À la place, si un passage du chapitre parle concrètement d'investissement, "
    "insère une mention brève et variée (une phrase) à cet endroit. Sinon, ne mets rien.\n\n"
    "3. CITATIONS : vérifie chaque citation attribuée. Corrections connues :\n"
    "   - « Le plus grand ennemi de la connaissance n'est pas l'ignorance, c'est l'illusion de la connaissance. » "
    "→ attribuée à Daniel Boorstin (pas Stephen Hawking).\n"
    "   - « Je n'ai jamais rencontré une personne riche qui ne lise pas beaucoup » → attribuée à Charlie Munger (pas Warren Buffett).\n"
    "   Pour toute autre citation douteuse, reformule en « souvent attribuée à X » ou retire l'attribution.\n\n"
    "4. NE RIEN changer d'autre. Garde tout le reste du texte MOT POUR MOT (structure Markdown, titres, listes, code, etc.).\n\n"
    "Rends UNIQUEMENT le chapitre complet corrigé en Markdown, sans aucun commentaire, sans préambule, sans explication."
)

# ---------------------------------------------------------------------------
# SSE / API
# ---------------------------------------------------------------------------

def _parse_sse_stream(response):
    """Lit un flux SSE et retourne (contenu_texte, usage_dict)."""
    content_parts: list[str] = []
    usage = {}
    buffer = ""

    while True:
        chunk = response.read(4096)
        if not chunk:
            break
        buffer += chunk.decode("utf-8", errors="replace")

        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.rstrip("\r")

            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                return "".join(content_parts), usage

            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue

            # usage (souvent dans le dernier chunk)
            if "usage" in obj and obj["usage"]:
                usage = obj["usage"]

            choices = obj.get("choices")
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            # Ignorer reasoning_content
            text = delta.get("content")
            if text:
                content_parts.append(text)

    return "".join(content_parts), usage

def call_api(text: str) -> tuple[str, dict]:
    """Appelle l'API avec retries exponentiels. Retourne (texte, usage)."""
    body = json.dumps({
        "model": MODEL,
        "stream": True,
        "messages": [
            {"role": "system", "content": EDITOR_SYSTEM},
            {"role": "user", "content": text},
        ],
    }).encode("utf-8")

    last_err: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(
                API_URL,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                content, usage = _parse_sse_stream(resp)
            if content.strip():
                return content, usage
            raise ValueError("Réponse vide de l'API")
        except Exception as exc:
            last_err = exc
            if attempt < MAX_ATTEMPTS:
                wait = 2 ** attempt
                time.sleep(wait)

    raise RuntimeError(f"Échec après {MAX_ATTEMPTS} tentatives : {last_err}")

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def count_words(text: str) -> int:
    return len(text.split())

def find_bot_motifs(text: str) -> list[str]:
    found = []
    for pat in BOT_PATTERNS_RE:
        matches = pat.findall(text)
        if matches:
            found.extend(matches)
    return found

def validate(original: str, cleaned: str) -> tuple[bool, str]:
    """Retourne (ok, raison)."""
    if not cleaned.strip():
        return False, "réponse vide"
    orig_words = count_words(original)
    clean_words = count_words(cleaned)
    if orig_words > 0 and clean_words < WORD_RATIO_MIN * orig_words:
        return False, (
            f"perte de mots : {clean_words}/{orig_words} "
            f"({clean_words/orig_words:.0%} < {WORD_RATIO_MIN:.0%})"
        )
    motifs = find_bot_motifs(cleaned)
    if motifs:
        return False, f"motifs résiduels : {motifs[:5]}"
    return True, ""

# ---------------------------------------------------------------------------
# Traitement d'un fichier
# ---------------------------------------------------------------------------

def process_file(src_path: Path, dst_path: Path) -> dict:
    """Traite un chapitre. Retourne un dict pour le rapport."""
    record: dict = {
        "fichier": src_path.name,
        "mots_avant": 0,
        "mots_après": 0,
        "motifs_avant": [],
        "motifs_après": [],
        "tentatives": 0,
        "tokens": {},
        "duree_s": 0.0,
        "statut": "ok",
    }

    original = src_path.read_text(encoding="utf-8")
    record["mots_avant"] = count_words(original)
    record["motifs_avant"] = find_bot_motifs(original)

    # Reprise : fichier destination déjà présent et valide
    if dst_path.exists():
        existing = dst_path.read_text(encoding="utf-8")
        ok, _ = validate(original, existing)
        if ok:
            record["mots_après"] = count_words(existing)
            record["motifs_après"] = find_bot_motifs(existing)
            record["statut"] = "existant_valide"
            return record

    total_usage: dict = {}
    t0 = time.monotonic()

    for attempt in range(1, MAX_ATTEMPTS + 1):
        record["tentatives"] = attempt
        try:
            cleaned, usage = call_api(original)
        except RuntimeError as exc:
            record["statut"] = f"erreur_api : {exc}"
            break

        # Cumuler les tokens
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            total_usage[k] = total_usage.get(k, 0) + usage.get(k, 0)

        ok, reason = validate(original, cleaned)
        if ok:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            dst_path.write_text(cleaned, encoding="utf-8")
            record["mots_après"] = count_words(cleaned)
            record["motifs_après"] = find_bot_motifs(cleaned)
            record["tokens"] = total_usage
            record["duree_s"] = round(time.monotonic() - t0, 2)
            record["statut"] = "ok"
            return record
        # sinon on retente
    else:
        # Toutes les tentatives échouées → garder l'original
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        dst_path.write_text(original, encoding="utf-8")
        record["mots_après"] = record["mots_avant"]
        record["motifs_après"] = record["motifs_avant"]
        record["statut"] = "echec_garde_original"

    record["tokens"] = total_usage
    record["duree_s"] = round(time.monotonic() - t0, 2)
    return record

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) != 3:
        print("Usage : python3 nettoyage.py <dossier_source> <dossier_destination>")
        sys.exit(1)

    src_dir = Path(sys.argv[1])
    dst_dir = Path(sys.argv[2])

    if not src_dir.is_dir():
        print(f"Erreur : le dossier source '{src_dir}' n'existe pas.")
        sys.exit(1)

    dst_dir.mkdir(parents=True, exist_ok=True)

    # Collecter les fichiers .md triés
    md_files = sorted(src_dir.glob("*.md"))
    if not md_files:
        print(f"Aucun fichier .md trouvé dans '{src_dir}'.")
        sys.exit(1)

    print(f"[nettoyage] {len(md_files)} fichier(s) à traiter, "
          f"parallélisme={PARALLEL}, destination='{dst_dir}'")

    results: list[dict] = []
    t_global = time.monotonic()

    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        futures = {}
        for src_path in md_files:
            dst_path = dst_dir / src_path.name
            fut = pool.submit(process_file, src_path, dst_path)
            futures[fut] = src_path.name

        for fut in as_completed(futures):
            fname = futures[fut]
            try:
                rec = fut.result()
            except Exception as exc:
                rec = {
                    "fichier": fname,
                    "mots_avant": 0,
                    "mots_après": 0,
                    "motifs_avant": [],
                    "motifs_après": [],
                    "tentatives": 0,
                    "tokens": {},
                    "duree_s": 0.0,
                    "statut": f"exception : {exc}",
                }
            results.append(rec)
            statut_icon = "✓" if rec["statut"] in ("ok", "existant_valide") else "✗"
            print(f"  {statut_icon} {rec['fichier']} : {rec['statut']} "
                  f"({rec['tentatives']} tentative(s), {rec['duree_s']}s)")

    # Trier par nom de fichier pour le rapport
    results.sort(key=lambda r: r["fichier"])

    # Rapport JSON
    rapport_path = Path("rapport_nettoyage.json")
    rapport = {
        "genere_le": datetime.now(timezone.utc).isoformat(),
        "duree_totale_s": round(time.monotonic() - t_global, 2),
        "fichiers": results,
    }
    rapport_path.write_text(
        json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nRapport écrit : {rapport_path}")

    # Résumé
    total_prompt = sum(r["tokens"].get("prompt_tokens", 0) for r in results)
    total_completion = sum(r["tokens"].get("completion_tokens", 0) for r in results)
    total_tokens = sum(r["tokens"].get("total_tokens", 0) for r in results)
    ok_count = sum(1 for r in results if r["statut"] in ("ok", "existant_valide"))
    fail_count = len(results) - ok_count

    print("\n" + "=" * 60)
    print("RÉSUMÉ")
    print("=" * 60)
    print(f"  Fichiers traités      : {len(results)}")
    print(f"  Succès                : {ok_count}")
    print(f"  Échecs / signalements : {fail_count}")
    print(f"  Tokens prompt         : {total_prompt:,}")
    print(f"  Tokens complétion     : {total_completion:,}")
    print(f"  Tokens total          : {total_tokens:,}")
    print(f"  Durée totale          : {rapport['duree_totale_s']}s")
    print("=" * 60)

    if fail_count:
        print("\nFichiers en échec :")
        for r in results:
            if r["statut"] not in ("ok", "existant_valide"):
                print(f"  - {r['fichier']} : {r['statut']}")

if __name__ == "__main__":
    main()
