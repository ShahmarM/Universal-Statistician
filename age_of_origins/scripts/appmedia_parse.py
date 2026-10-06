# Parse appmedia.jp Age of Z facility pages: level effect table + level cost table (resources, time, prerequisites).
import re,html,json,sys,glob,os
RES={'食糧':'food','石油':'oil','鉄鉱':'steel','レアアース':'minerals','ウラン':'uranium','リーダーバッジ':'leader_badges'}
def clean(s): return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>','',re.sub(r'<br\s*/?>',' / ',s)))).strip()
def secs(t):
    m=re.match(r'(?:(\d+)日\s*)?(\d+):(\d+):(\d+)',t)
    if not m: return None
    d,h,mi,s=[int(x or 0) for x in m.groups()]; return ((d*24+h)*60+mi)*60+s
out={}
for f in sorted(glob.glob(sys.argv[1]+'/f_*.html')):
    h=open(f).read()
    title=clean(re.search(r'<title>(.*?)</title>',h,re.S).group(1))
    name=re.sub(r'^【エイジオブゼット】','',title).split('の')[0]
    pid=re.search(r'f_(\d+)',f).group(1)
    tabs=re.findall(r'<table.*?</table>',h,re.S)
    rec={'page_id':pid,'title':title,'url':f'https://appmedia.jp/ageofzorigins/{pid}','modified':(re.findall(r'dateModified": "([^"]+)',h) or [None])[0],'effects':{},'costs':{},'other_tables':[]}
    for t in tabs:
        rows=re.findall(r'<tr.*?</tr>',t,re.S)
        hdr=[clean(c) for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>',rows[0],re.S)] if rows else []
        if hdr[:2]==['施設レベル','資源と時間']:
            for r in rows[1:]:
                cs=re.findall(r'<td[^>]*>(.*?)</td>',r,re.S)
                lv=int(clean(cs[0]))
                items=re.findall(r"<div class='per_item'>(.*?)</div>",cs[1],re.S)
                res={}; tm=None
                for it in items:
                    c=clean(it)
                    m=re.match(r'(\S+?) / ([\d,]+)$',c)
                    if m and m.group(1) in RES: res[RES[m.group(1)]]=int(m.group(2).replace(',',''))
                    elif secs(c) is not None: tm=c
                    else: res.setdefault('_other',[]).append(c)
                if not items: tm_txt=clean(cs[1]); tm=re.search(r'[\d日 :]+$',tm_txt).group(0).strip() if re.search(r'\d+:\d+:\d+',tm_txt) else None
                req=[x.strip('・ ') for x in clean(cs[2]).split(' / ') if x.strip('・ ')] if len(cs)>2 else []
                rec['costs'][lv]={'resources':res,'time':tm,'time_seconds':secs(tm) if tm else None,'requires':req}
        elif hdr[:2]==['施設レベル','効果']:
            for r in rows[1:]:
                cs=[clean(c) for c in re.findall(r'<td[^>]*>(.*?)</td>',r,re.S)]
                if len(cs)>=2: rec['effects'][int(cs[0])]=[x.strip('・ ') for x in cs[1].split(' / ') if x.strip('・ ')]
        elif hdr and hdr[0] not in ('注目の記事',):
            rec['other_tables'].append([[clean(c) for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>',r,re.S)] for r in rows])
    out[name]=rec
    print(pid,name,'effects',len(rec['effects']),'costs',len(rec['costs']),'other',len(rec['other_tables']))
json.dump(out,open(sys.argv[2],'w'),ensure_ascii=False,indent=1)
