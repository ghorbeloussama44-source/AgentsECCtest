"""Client minimal pour Nexum Router (Dialagram) — stdlib uniquement.

Usage :
    python dialagram/nexum.py models
    python dialagram/nexum.py ask -m qwen-3.8-max "Ta question"
    python dialagram/nexum.py agents "Tâche à découper"
    python dialagram/nexum.py council "Question à débattre"

Clé API : variable DIALAGRAM_API_KEY (inutile dans la session cloud, où le
proxy ajoute la clé automatiquement).
"""
import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE_URL = os.environ.get("DIALAGRAM_BASE_URL", "https://dialagram.me/router/v1")
API_KEY = os.environ.get("DIALAGRAM_API_KEY", "")


def _request(path, payload=None, timeout=300):
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            # Les 5xx (modèle en amont indisponible) sont souvent passagers.
            if e.code < 500 or attempt == 2:
                raise
            time.sleep(2 ** attempt)


def list_models():
    return [m["id"] for m in _request("/models")["data"]]


FALLBACK_MODEL = "xiaomi-mimo-2.6"


def chat(model, prompt, system=None, max_tokens=2000):
    """Envoie un prompt et renvoie {model, text, usage, seconds}.
    Si le modèle reste en 5xx, bascule une fois sur FALLBACK_MODEL."""
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": prompt})
    start = time.time()
    try:
        resp = _request("/chat/completions", {
            "model": model, "messages": messages, "max_tokens": max_tokens,
        })
    except urllib.error.HTTPError as e:
        if e.code < 500 or model == FALLBACK_MODEL:
            raise
        print(f"[{model}] HTTP {e.code}, bascule sur {FALLBACK_MODEL}", file=sys.stderr)
        model = FALLBACK_MODEL
        resp = _request("/chat/completions", {
            "model": model, "messages": messages, "max_tokens": max_tokens,
        })
    return {
        "model": model,
        "text": resp["choices"][0]["message"]["content"],
        "usage": resp.get("usage", {}),
        "seconds": round(time.time() - start, 1),
    }


def _safe_chat(job):
    try:
        return chat(*job)
    except urllib.error.HTTPError as e:
        print(f"[{job[0]}] échec HTTP {e.code}, ignoré", file=sys.stderr)
        return None


def run_parallel(jobs):
    """jobs = [(model, prompt, system), ...] -> résultats dans le même ordre
    (None pour un modèle qui a échoué)."""
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        return list(pool.map(_safe_chat, jobs))


def sum_usage(results):
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for r in results:
        if r is None:
            continue
        for k in total:
            total[k] += r["usage"].get(k, 0)
    return total


# --- Sous-agents : un orchestrateur découpe, des workers exécutent, il fusionne.

def subagents(task, orchestrator="qwen-3.8-max", worker="xiaomi-mimo-2.6", n=3):
    plan = chat(orchestrator, task, system=(
        f"Découpe la tâche en exactement {n} sous-tâches indépendantes. "
        'Réponds uniquement en JSON : {"subtasks": ["...", ...]}'))
    raw = plan["text"].strip().removeprefix("```json").strip("`\n ")
    subtasks = json.loads(raw)["subtasks"][:n]
    results = run_parallel([
        (worker, f"Tâche globale : {task}\n\nTa sous-tâche : {s}", None)
        for s in subtasks
    ])
    done = [(s, r) for s, r in zip(subtasks, results) if r]
    merged = chat(orchestrator, "\n\n".join(
        [f"Tâche : {task}", "Résultats des sous-agents :"]
        + [f"## {s}\n{r['text']}" for s, r in done]
        + ["Fusionne ces résultats en une réponse finale cohérente."]))
    all_calls = [plan, *results, merged]
    return {"subtasks": subtasks, "final": merged["text"], "usage": sum_usage(all_calls)}


# --- Council (méthode Karpathy) : réponses indépendantes, revue anonyme, synthèse.

COUNCIL = ["qwen-3.8-max", "qwen-3.7-plus", "xiaomi-mimo-2.6", "meta-muse-spark-1.2"]
CHAIRMAN = "qwen-3.8-max"


def council(question, members=COUNCIL, chairman=CHAIRMAN):
    # 1. Chaque membre répond seul.
    answers = [a for a in run_parallel([(m, question, None) for m in members]) if a and a["text"]]
    if len(answers) < 2:
        raise RuntimeError("Moins de 2 membres ont répondu, council impossible.")

    # 2. Revue anonyme : réponses mélangées et étiquetées A, B, C...
    order = list(range(len(answers)))
    random.shuffle(order)
    labels = {chr(65 + i): answers[idx] for i, idx in enumerate(order)}
    anonymised = "\n\n".join(f"### Réponse {k}\n{a['text']}" for k, a in labels.items())
    context = f"Question : {question}\n\n{anonymised}\n\n"
    review_prompt = context + ("Classe ces réponses de la meilleure à la pire, "
                               "avec une phrase de justification pour chacune.")
    reviews = run_parallel([(m, review_prompt, None) for m in members])

    # 3. Le président synthétise.
    final = chat(chairman, context + "Revues des pairs :\n\n"
                 + "\n\n".join(r["text"] for r in reviews if r and r["text"])
                 + "\n\nRédige la meilleure réponse finale en t'appuyant sur tout ce qui précède.")
    return {
        "labels": {k: a["model"] for k, a in labels.items()},
        "final": final["text"],
        "usage": sum_usage([*answers, *reviews, final]),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("models")
    a = sub.add_parser("ask")
    a.add_argument("prompt")
    a.add_argument("-m", "--model", default="xiaomi-mimo-2.6")
    sub.add_parser("agents").add_argument("task")
    sub.add_parser("council").add_argument("question")
    args = p.parse_args()

    if args.cmd == "models":
        print("\n".join(list_models()))
    elif args.cmd == "ask":
        r = chat(args.model, args.prompt)
        print(r["text"], file=sys.stdout)
        print(f"\n[{r['model']}] {r['usage']} — {r['seconds']}s", file=sys.stderr)
    elif args.cmd == "agents":
        r = subagents(args.task)
        print(r["final"])
        print(f"\nSous-tâches : {r['subtasks']}\nTokens : {r['usage']}", file=sys.stderr)
    elif args.cmd == "council":
        r = council(args.question)
        print(r["final"])
        print(f"\nAnonymat : {r['labels']}\nTokens : {r['usage']}", file=sys.stderr)


if __name__ == "__main__":
    main()
