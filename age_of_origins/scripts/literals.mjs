// Pull large array/object literals out of minified bundles and evaluate them in an isolated vm context.
import fs from 'fs'; import vm from 'vm'; import path from 'path';
const [,, src, out] = process.argv; fs.mkdirSync(out,{recursive:true});
function balanced(s, i){ const open=s[i], close=open==='['?']':'}'; let d=0, q=null;
  for(let j=i;j<s.length;j++){const c=s[j];
    if(q){ if(c==='\\'){j++;continue} if(c===q) q=null; continue }
    if(c==='"'||c==="'"||c==='`'){q=c;continue}
    if(c==='['||c==='{')d++; else if(c===']'||c==='}'){d--; if(d===0) return j+1}}
  return -1 }
for (const fn of fs.readdirSync(src).filter(f=>f.endsWith('.js'))) {
  const s=fs.readFileSync(path.join(src,fn),'utf8'); let n=0; const re=/(?:[\w$]+)=(\[|\{)/g; let m; let last=0;
  while((m=re.exec(s))){ const st=m.index+m[0].length-1; if(st<last) continue; const en=balanced(s,st); if(en<0) continue;
    const lit=s.slice(st,en); if(lit.length<400) continue;
    try{ const v=vm.runInNewContext('('+lit+')',{},{timeout:2000}); const js=JSON.stringify(v);
      if(!js || js.length<300) continue;
      const name=m[0].split('=')[0]; const f=`${fn.split('.')[0]}__${name}_${n++}.json`;
      fs.writeFileSync(path.join(out,f), JSON.stringify(v,null,1)); last=en;
      console.log(f, js.length, js.slice(0,160)); }catch(e){} }
}
