import re,json,sys,os
src,out=sys.argv[1],sys.argv[2]
os.makedirs(out,exist_ok=True)
for fn in sorted(os.listdir(src)):
    if not fn.endswith('.js'): continue
    s=open(os.path.join(src,fn)).read()
    blocks=re.findall(r'JSON\.parse\(`(.*?)`\)',s,re.S)
    for i,b in enumerate(blocks):
        try:
            d=json.loads(b.replace('\\`','`'))
        except Exception as e:
            print(fn,i,'ERR',e); continue
        name=f"{fn.split('.')[0]}_{i}.json"
        json.dump(d,open(os.path.join(out,name),'w'),indent=1,ensure_ascii=False)
        desc = type(d).__name__, len(d)
        keys = list(d[0].keys())[:12] if isinstance(d,list) and d and isinstance(d[0],dict) else (list(d.keys())[:12] if isinstance(d,dict) else None)
        print(name,desc,keys)
    print(fn,'jsonblocks',len(blocks),'size',len(s))
