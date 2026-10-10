#!/bin/bash
# Usine de la bibliothèque : traite sujets.json par lots de 5 livres en parallèle.
# Chaque livre : plan (Qwen) -> rédaction -> rectification -> PDF, puis copie dans le dépôt.
# Après chaque lot : commit + push. Reprise : un livre dont le PDF est déjà dans le dépôt est sauté.
R=/home/user/AgentsECCtest; O=$R/bibliotheque/outils; W=${W:-/tmp/usine}; BR=claude/millionaire-book-500-pages-kb2v7u
mkdir -p $W; cd $W

livre() {  # $1 = slug
  s=$1; d=$W/$s; mkdir -p $d; cd $d
  python3 - "$s" <<'PY'
import json,sys
s=sys.argv[1]; x=[b for b in json.load(open('/home/user/AgentsECCtest/bibliotheque/outils/sujets.json')) if b['slug']==s][0]
open('brief_plan.txt','w').write(f"""MISSION (bibliothèque Oussama Ghorbel, phase 1 : recherche et plan) — Tu es l'auteur pour le compte de l'écrivain Oussama Ghorbel. Claude ne fait que te transmettre la mission.

Objectif final : un livre complet et original, en français, d'environ 300 pages (environ 85 000 mots), sur {x['sujet']}. Il rejoint une collection de 100 livres pratiques (argent, entreprise, compétences, relations, bien-être, numérique) : reste centré sur ton sujet.

1. Recense au moins 15 livres de référence sur le sujet (auteur, année, idée centrale en 2 lignes, ce que notre livre en retiendra). Tu n'as pas accès au web : appuie-toi sur tes connaissances et signale toute incertitude.
2. Conçois le plan détaillé d'un livre ORIGINAL qui synthétise et dépasse ces sources (aucune reproduction de texte protégé, citations très courtes et attribuées seulement ; pas de conseil médical, juridique ou fiscal personnalisé). Titre accrocheur, sous-titre, 4 à 6 parties, 24 chapitres. Pour chaque chapitre : titre, objectif, 5 à 7 sections avec titre, exercices pratiques prévus.
3. Chaque chapitre doit viser 3 500 mots.

Réponds UNIQUEMENT avec un JSON valide (sans balises markdown) de la forme :
{{"titre":"...","sous_titre":"...","bibliographie":[{{"auteur":"...","titre":"...","annee":"...","idee":"...","apport":"..."}}],"parties":[{{"titre":"...","chapitres":[{{"numero":1,"titre":"...","objectif":"...","sections":["...","..."],"exercices":["..."]}}]}}]}}
""")
PY
  for i in 1 2 3; do
    [ -s plan.json ] && break
    python3 $O/ask.py brief_plan.txt plan_raw.txt
    python3 -c "
import json;t=open('plan_raw.txt').read().strip()
if t.startswith('\`\`\`'): t=t.split('\n',1)[1].rsplit('\`\`\`',1)[0]
p=json.loads(t);assert sum(len(x['chapitres']) for x in p['parties'])>=20
json.dump(p,open('plan.json','w'),ensure_ascii=False,indent=1)" 2>/dev/null || { rm -f plan.json; sleep 20; }
  done
  [ -s plan.json ] || { echo "[$s] ERREUR plan"; return; }
  # Garde-fou : chaque étape est relancée (reprise) tant qu'il manque des chapitres ; jamais de livre incomplet publié
  for i in 1 2 3; do
    python3 -u $O/redaction.py plan.json chapitres --parallele 3 >> redaction.log 2>&1
    attendus=$(python3 -c "import json;p=json.load(open('plan.json'));print(sum(len(x['chapitres']) for x in p['parties'])+2)")
    [ $(ls chapitres/*.md 2>/dev/null | wc -l) -ge $attendus ] && break; sleep 180
  done
  [ $(ls chapitres/*.md 2>/dev/null | wc -l) -ge $attendus ] || { echo "[$s] ERREUR rédaction incomplète ($(ls chapitres/*.md | wc -l)/$attendus), reporté"; return; }
  for i in 1 2 3; do
    python3 -u $O/nettoyage.py chapitres chapitres_v2 >> nettoyage.log 2>&1
    [ $(ls chapitres_v2/*.md 2>/dev/null | wc -l) -ge $attendus ] && break; sleep 180
  done
  [ $(ls chapitres_v2/*.md 2>/dev/null | wc -l) -ge $attendus ] || { echo "[$s] ERREUR rectification incomplète ($(ls chapitres_v2/*.md | wc -l)/$attendus), reporté"; return; }
  rm -f livre.pdf
  python3 $O/mise_en_page.py plan.json chapitres_v2 livre.pdf --corps 11.3 > pdf.log 2>&1
  [ -s livre.pdf ] || { echo "[$s] ERREUR pdf"; return; }
  python3 - "$s" <<'PY'
import json,glob,re,unicodedata,shutil,os,sys
from pypdf import PdfReader
s=sys.argv[1]; R='/home/user/AgentsECCtest/bibliotheque/'+s; os.makedirs(R,exist_ok=True)
p=json.load(open('plan.json')); fs=glob.glob('chapitres_v2/*.md')
mots=sum(len(open(f).read().split()) for f in fs)
fuite=re.compile(r"dites-moi|souhaitez-vous que je|voulez-vous que je|si vous souhaitez que j|je m'y mets|intégralement (terminé|rédigé)|(clients?|entrepreneurs?|personnes?) que j'(ai )?accompagn",re.I)
fuites=sum(1 for f in fs if fuite.search(open(f).read()))
n=json.load(open('rapport_nettoyage.json'))['fichiers']
r=json.dumps(json.load(open('chapitres/rapport_redaction.json'))) if os.path.exists('chapitres/rapport_redaction.json') else ''
tok=sum(map(int,re.findall(r'"total_tokens": (\d+)',r)))+sum(f['tokens']['total_tokens'] for f in n if f.get('tokens'))
c=dict(slug=s,titre=p['titre'],sous_titre=p['sous_titre'],pages=len(PdfReader('livre.pdf').pages),mots=mots,fichiers=len(fs),
  min_mots=min(len(open(f).read().split()) for f in fs),fuites=fuites,rectif_ok=sum(1 for f in n if f['statut'] in('ok','existant_valide')),rectif_total=len(n),tokens_dialagram=tok)
fn=re.sub(r"[^A-Za-z0-9]+","-",unicodedata.normalize('NFKD',p['titre']).encode('ascii','ignore').decode()).strip('-')+'-Oussama-Ghorbel.pdf'
shutil.rmtree(R+'/chapitres_v2',ignore_errors=True); shutil.copytree('chapitres_v2',R+'/chapitres_v2')
for f in ['plan.json','brief_plan.txt']: shutil.copy(f,R)
for f in glob.glob(R+'/*.pdf'): os.remove(f)
shutil.copy('livre.pdf',R+'/'+fn); c['pdf']=fn
json.dump(c,open(R+'/controle.json','w'),ensure_ascii=False,indent=1)
print(f"[{s}] FIN {c['titre']} — {c['pages']} p, {mots} mots, fuites {fuites}, rectif {c['rectif_ok']}/{c['rectif_total']}")
PY
}

todo=$(python3 -c "
import json,glob
for b in json.load(open('$O/sujets.json')):
    if not glob.glob('$R/bibliotheque/'+b['slug']+'/*.pdf'): print(b['slug'])")
set -- $todo
lot=0
while [ $# -gt 0 ]; do
  lot=$((lot+1)); batch="$1 $2 $3 $4 $5"; shift 5 2>/dev/null || shift $#
  echo "$(date -u +%H:%M) LOT $lot : $batch"
  for s in $batch; do livre $s & done; wait
  cd $R && git add -A bibliotheque && git commit -qm "Bibliothèque : lot automatique ($batch)

Livres rédigés, rectifiés et mis en page par Qwen 3.8 Max Thinking via Dialagram.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RdzzPjcfx3EDQ99UzoYL5J"
  for t in 2 4 8 16; do git push -q origin $BR && break; sleep $t; done
  echo "$(date -u +%H:%M) LOT $lot poussé ($(ls -d $R/bibliotheque/*/controle.json 2>/dev/null | wc -l) livres avec contrôle)"
  cd $W; [ -n "$LOTS" ] && [ $lot -ge $LOTS ] && { echo "PAUSE_APRES_LOT $lot"; exit 0; }
done
echo "USINE_TERMINEE"
