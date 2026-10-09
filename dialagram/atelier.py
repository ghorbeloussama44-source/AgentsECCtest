"""Moteur de l'Atelier Dialagram : délègue les tâches à Dialagram et prépare
les mises à jour de la base de la page.

Claude exporte les documents de la page (ArtifactData list avec out_dir),
lance ce script, puis renvoie les fichiers produits dans la base.

    python dialagram/atelier.py delegate EXPORT_DIR OUT_DIR
        Envoie chaque tâche « a_faire » au modèle Dialagram demandé, en
        parallèle. Écrit OUT_DIR/<id>.json (statut « a_valider », réponse,
        tokens Dialagram) ou un échec (statut « refuse »).

    python dialagram/atelier.py verdict EXPORT_DIR OUT_DIR ID ok|refaire "note"
        Enregistre la décision de Claude et une estimation de ses tokens :
        lecture de la consigne et de la réponse, plus la note écrite.

Les tokens Claude sont estimés (4 caractères par token) : la session ne
donne pas le compte exact de ce qu'elle consomme pour une tâche.
"""
import json
import math
import sys
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import nexum  # noqa: E402

DEFAULT_MODEL = "qwen-3.8-max"
SYSTEM = ("Tu travailles pour un projet suivi dans un atelier. Réponds en français, "
          "directement, avec le livrable demandé et rien d'autre.")


def estimate_tokens(text):
    return math.ceil(len(text or "") / 4)


def load_docs(export_dir, collection):
    """Lit les fichiers exportés (<export_dir>/<collection>/<id>.json)."""
    docs = {}
    for f in sorted((Path(export_dir) / collection).glob("*.json")):
        raw = json.loads(f.read_text())
        body = raw.get("data", raw) if isinstance(raw, dict) else {}
        docs[f.stem] = body
    return docs


def write(out_dir, doc_id, data):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{doc_id}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))


def run_task(task, project):
    model = task.get("model") or "auto"
    if model == "auto":
        model = DEFAULT_MODEL
    prompt = (f"Projet : {project.get('name', '')}\n"
              f"Objectif du projet : {project.get('brief', '') or '(non précisé)'}\n\n"
              f"Tâche : {task.get('title', '')}\n\nConsigne :\n{task.get('instructions', '')}")
    attempts = int(task.get("attempts") or 0) + 1
    try:
        r = nexum.chat(model, prompt, system=SYSTEM, max_tokens=4000)
    except (urllib.error.URLError, TimeoutError) as e:
        reason = f"HTTP {e.code}" if isinstance(e, urllib.error.HTTPError) else str(e)
        return {"status": "refuse", "attempts": attempts, "updatedAt": int(time.time() * 1000),
                "claude": {"verdict": "echec", "note": f"Dialagram n'a pas répondu ({model}, {reason}). Relancez la tâche.",
                           "tokens": 0}}
    usage = r["usage"]
    return {
        "status": "a_valider",
        "attempts": attempts,
        "updatedAt": int(time.time() * 1000),
        "result": r["text"] or "",
        "dialagram": {
            "model": r["model"],
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "seconds": r["seconds"],
        },
    }


def delegate(export_dir, out_dir):
    tasks = load_docs(export_dir, "tasks")
    projects = load_docs(export_dir, "projects")
    todo = {i: t for i, t in tasks.items() if t.get("status") in ("a_faire", "delegue")}
    if not todo:
        print("Aucune tâche à faire.")
        return
    with ThreadPoolExecutor(max_workers=min(4, len(todo))) as pool:
        futures = {i: pool.submit(run_task, t, projects.get(t.get("projectId"), {})) for i, t in todo.items()}
    for i, f in futures.items():
        upd = f.result()
        write(out_dir, i, upd)
        d = upd.get("dialagram", {})
        print(f"{i}: {upd['status']} {d.get('model', '')} {d.get('total_tokens', 0)} tokens")


def verdict(export_dir, out_dir, doc_id, decision, note):
    task = load_docs(export_dir, "tasks")[doc_id]
    previous = (task.get("claude") or {}).get("tokens", 0)
    spent = estimate_tokens(task.get("instructions")) + estimate_tokens(task.get("result")) + estimate_tokens(note)
    write(out_dir, doc_id, {
        "status": "valide" if decision == "ok" else "refuse",
        "updatedAt": int(time.time() * 1000),
        "claude": {"verdict": decision, "note": note, "tokens": previous + spent, "estimated": True},
    })
    print(f"{doc_id}: {decision}, ≈{spent} tokens Claude (total ≈{previous + spent})")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "delegate" and len(sys.argv) == 4:
        delegate(sys.argv[2], sys.argv[3])
    elif cmd == "verdict" and len(sys.argv) == 7 and sys.argv[5] in ("ok", "refaire"):
        verdict(*sys.argv[2:7])
    else:
        print(__doc__)
        sys.exit(2)
