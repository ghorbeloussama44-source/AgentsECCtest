#!/bin/bash
# Chaîne par livre : rédaction -> rectification -> PDF (scripts écrits par Qwen)
cd /tmp/claude-0/-home-user-AgentsECCtest/7b9407f0-d9f7-5a22-ac79-5e12ecb74dd8/scratchpad/mission/biblio/$1 || exit 1
echo "[$1] rédaction"; python3 -u /tmp/claude-0/-home-user-AgentsECCtest/7b9407f0-d9f7-5a22-ac79-5e12ecb74dd8/scratchpad/mission/redaction.py plan.json chapitres --parallele 6 > redaction.log 2>&1 || echo "[$1] ERREUR rédaction"
echo "[$1] rectification"; python3 -u /tmp/claude-0/-home-user-AgentsECCtest/7b9407f0-d9f7-5a22-ac79-5e12ecb74dd8/scratchpad/mission/nettoyage.py chapitres chapitres_v2 > nettoyage.log 2>&1 || echo "[$1] ERREUR rectification"
echo "[$1] pdf"; python3 /tmp/claude-0/-home-user-AgentsECCtest/7b9407f0-d9f7-5a22-ac79-5e12ecb74dd8/scratchpad/mission/mise_en_page.py plan.json chapitres_v2 livre.pdf --corps 11.3 > pdf.log 2>&1 || echo "[$1] ERREUR pdf"
echo "[$1] FIN $(tail -1 pdf.log)"
