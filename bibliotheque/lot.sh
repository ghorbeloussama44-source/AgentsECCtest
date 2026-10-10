#!/bin/bash
# Plan puis chaîne complète pour un livre (relance le plan si le JSON est invalide)
cd /tmp/claude-0/-home-user-AgentsECCtest/7b9407f0-d9f7-5a22-ac79-5e12ecb74dd8/scratchpad/mission
for i in 1 2 3; do
  python3 ask.py biblio/$1/brief_plan.txt biblio/$1/plan_raw.txt
  python3 -c "
import json;t=open('biblio/$1/plan_raw.txt').read().strip()
if t.startswith('\`\`\`'): t=t.split('\n',1)[1].rsplit('\`\`\`',1)[0]
p=json.loads(t);json.dump(p,open('biblio/$1/plan.json','w'),ensure_ascii=False,indent=1)
print('[$1] PLAN',p['titre'],sum(len(x['chapitres']) for x in p['parties']),'chap')" && break
  echo "[$1] plan invalide, nouvel essai"; sleep 20
done
[ -f biblio/$1/plan.json ] || { echo "[$1] ERREUR plan"; exit 1; }
./chaine.sh $1
