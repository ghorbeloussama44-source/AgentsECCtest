# Atelier Dialagram

Page de suivi : https://claude.ai/artifact/9RVZBryS5GEQrtmAxbnPXL (source : `atelier.html`, capability `db`).

Base de la page :
- `projects/<id>` : `name`, `brief`, `createdAt`
- `tasks/<id>` : `projectId`, `title`, `instructions`, `model` (`auto` = qwen-3.8-max), `status`,
  `attempts`, `result`, `dialagram{model, prompt_tokens, completion_tokens, total_tokens, seconds}`,
  `claude{verdict, note, tokens, estimated}`
- Statuts : `a_faire` → `delegue` → `a_valider` → `valide` | `refuse`

## Quand l'utilisateur écrit « lance l'atelier »

1. `ArtifactData list` de `projects` et `tasks` avec `out_dir` = EXPORT.
2. Passer les tâches `a_faire` en `delegue` (batch `update`, avec `if_version`).
3. `python dialagram/atelier.py delegate EXPORT OUT`, puis batch `update` avec `file_path` = `OUT/<id>.json`.
4. Ré-exporter `tasks`, relire chaque réponse (exécuter le code quand c'en est), puis
   `python dialagram/atelier.py verdict EXPORT VERDICTS <id> ok|refaire "note"` et batch `update`.

Tokens Claude : estimés à 4 caractères par token (consigne + réponse lue + note écrite).
Les tokens Dialagram viennent du champ `usage` de l'API.
