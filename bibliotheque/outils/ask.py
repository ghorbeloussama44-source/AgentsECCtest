import json,sys,time,urllib.request
msg=open(sys.argv[1]).read()
req=urllib.request.Request('https://dialagram.me/router/v1/chat/completions',data=json.dumps({'model':'qwen-3.8-max-thinking','stream':True,'messages':[{'role':'user','content':msg}]}).encode(),headers={'Content-Type':'application/json'})
out=[];usage={};t=time.time()
for l in urllib.request.urlopen(req,timeout=1500):
    l=l.decode().strip()
    if l.startswith('data: {'):
        d=json.loads(l[6:]); usage=d.get('usage') or usage
        for c in d.get('choices',[]): out.append(c['delta'].get('content') or '')
open(sys.argv[2],'w').write(''.join(out))
usage['seconds']=round(time.time()-t,1)
with open('usage.log','a') as f: f.write(sys.argv[2]+' '+json.dumps(usage)+'\n')
