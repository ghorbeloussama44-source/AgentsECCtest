#!/usr/bin/env python3
"""Publie l'état en direct de l'usine (etat.json) sur la branche git `etat-usine`.

Lu par l'Atelier Dialagram via le connecteur GitHub. Usage : etat.py [--boucle SECONDES]
"""
import glob, json, os, re, subprocess, sys, time

R = "/home/user/AgentsECCtest"
W = os.environ.get("W", "/tmp/usine")
WT = "/tmp/etat-wt"
BR = "etat-usine"


def tail(path, n=2):
    try:
        lines = [l.strip() for l in open(path, encoding="utf-8", errors="replace") if l.strip()]
        return lines[-n:]
    except OSError:
        return []


def livre(slug):
    d = f"{W}/{slug}"
    fini = glob.glob(f"{R}/bibliotheque/{slug}/controle.json")
    plan = json.load(open(f"{d}/plan.json")) if os.path.exists(f"{d}/plan.json") else None
    chap = sorted(glob.glob(f"{d}/chapitres/*.md"))
    v2 = glob.glob(f"{d}/chapitres_v2/*.md")
    total = (sum(len(p["chapitres"]) for p in plan["parties"]) + 2) if plan else 26
    if fini:
        etape = "terminé"
    elif os.path.exists(f"{d}/pdf.log"):
        etape = "mise en page"
    elif os.path.exists(f"{d}/nettoyage.log"):
        etape = "rectification"
    elif plan:
        etape = "rédaction"
    else:
        etape = "plan"
    # prompts en cours : le brief du plan, puis les chapitres pas encore écrits
    prompts = []
    if etape == "plan" and os.path.exists(f"{d}/brief_plan.txt"):
        prompts.append(open(f"{d}/brief_plan.txt", encoding="utf-8").read())
    elif etape == "rédaction" and plan:
        faits = {os.path.basename(f) for f in chap}
        for p in plan["parties"]:
            for c in p["chapitres"]:
                if f"ch{int(c['numero']):02d}.md" not in faits:
                    prompts.append(f"Rédige le chapitre {c['numero']} : « {c['titre']} ».\nObjectif : {c.get('objectif','')}\n"
                                   + "Sections : " + " · ".join(c.get("sections", [])))
        prompts = prompts[:6]
    elif etape == "rectification":
        prompts.append("Relecture d'éditeur de chaque chapitre : retirer les messages au commanditaire et les doublons, "
                       "vérifier les citations, alléger l'avertissement financier, reformuler les expériences personnelles invérifiables.")
    c = json.load(open(fini[0])) if fini else {}
    log = ([] if fini else tail(f"{d}/nettoyage.log") if etape == "rectification"
           else [l for l in tail(f"{d}/redaction.log", 6) if l.startswith(("[", "⚠"))][-2:])
    return {
        "slug": slug, "etape": etape, "titre": (plan or {}).get("titre") or c.get("titre"),
        "chapitres_ecrits": len(chap), "chapitres_rectifies": len(v2), "chapitres_total": total,
        "mots": sum(len(open(f, encoding="utf-8").read().split()) for f in chap),
        "prompts": prompts, "journal": log,
        "pages": c.get("pages"), "fuites": c.get("fuites"), "tokens": c.get("tokens_dialagram"),
    }


def etat():
    lot_line = next((l for l in reversed(tail(f"{W}/usine.log", 50)) if " LOT " in l and " : " in l), "")
    lot = lot_line.split(" : ", 1)[1].split() if lot_line else []
    sujets = json.load(open(f"{R}/bibliotheque/outils/sujets.json"))
    faits = [json.load(open(f)) for f in glob.glob(f"{R}/bibliotheque/*/controle.json")]
    tourne = subprocess.run(["pgrep", "-f", "usine.sh"], capture_output=True).returncode == 0
    return {
        "maj": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "actif": tourne, "debut_lot": lot_line[:5],
        "objectif": len(sujets), "termines": len(faits),
        "tokens_dialagram": sum(f.get("tokens_dialagram") or 0 for f in faits),
        "lot": [livre(s) for s in lot],
        "catalogue": catalogue(faits),
    }


def catalogue(faits):
    """Tous les livres terminés, avec le chemin de leur PDF dans le dépôt."""
    out = []
    try:
        for b in json.load(open(f"{R}/bibliotheque/books.json")):
            chemin = ("livre-millionnaire/L-Architecture-de-la-Richesse.pdf" if b["slug"] == "architecture-de-la-richesse"
                      else f"bibliotheque/{b['slug']}/{b['nom_fichier']}")
            out.append({"slug": b["slug"], "titre": b["titre"], "sous_titre": b["sous_titre"], "theme": b["theme"],
                        "pages": b["pages"], "pdf": chemin})
    except (OSError, KeyError, ValueError):
        pass
    themes = {x["slug"]: x["theme"] for x in json.load(open(f"{R}/bibliotheque/outils/sujets.json"))}
    for f in sorted(faits, key=lambda x: x["slug"]):
        out.append({"slug": f["slug"], "titre": f["titre"], "sous_titre": f.get("sous_titre", ""),
                    "theme": themes.get(f["slug"], ""), "pages": f["pages"], "mots": f.get("mots"),
                    "tokens": f.get("tokens_dialagram"), "pdf": f"bibliotheque/{f['slug']}/{f['pdf']}"})
    return out


def publier():
    if not os.path.isdir(WT):
        subprocess.run(["git", "-C", R, "worktree", "add", "--detach", WT], check=True, capture_output=True)
        subprocess.run(["git", "-C", WT, "checkout", "--orphan", BR], check=True, capture_output=True)
        subprocess.run(["git", "-C", WT, "rm", "-rfq", "."], capture_output=True)
    json.dump(etat(), open(f"{WT}/etat.json", "w"), ensure_ascii=False, indent=1)
    subprocess.run(["git", "-C", WT, "add", "etat.json"], check=True)
    subprocess.run(["git", "-C", WT, "commit", "-q", "--amend", "--allow-empty", "-m", "État en direct de l'usine"],
                   capture_output=True) if subprocess.run(["git", "-C", WT, "rev-parse", "HEAD"], capture_output=True).returncode == 0 \
        else subprocess.run(["git", "-C", WT, "commit", "-q", "-m", "État en direct de l'usine"], check=True)
    subprocess.run(["git", "-C", WT, "push", "-qf", "origin", f"HEAD:{BR}"], capture_output=True)


if __name__ == "__main__":
    pause = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--boucle" else 0
    while True:
        try:
            publier()
        except Exception as e:  # l'état est un confort : ne jamais bloquer l'usine
            print("etat:", e, file=sys.stderr)
        if not pause:
            break
        time.sleep(pause)
