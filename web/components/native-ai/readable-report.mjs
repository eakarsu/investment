const node=(tag,cls='',text)=>{const e=document.createElement(tag);e.className=cls;if(text!==undefined)e.textContent=text;return e;};
function inline(parent,text){
 const pattern=/(\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\))/g;let last=0;
 for(const m of text.matchAll(pattern)){parent.append(document.createTextNode(text.slice(last,m.index)));if(m[2])parent.append(node('strong','',m[2]));else if(m[3])parent.append(node('code','',m[3]));else{const a=node('a','',m[4]);a.href=m[5];a.target='_blank';a.rel='noopener noreferrer';parent.append(a);}last=m.index+m[0].length;}
 parent.append(document.createTextNode(text.slice(last)));
}
const readableLabel=value=>String(value).replace(/([a-z0-9])([A-Z])/g,'$1 $2').replace(/[_-]+/g,' ').replace(/^./,c=>c.toUpperCase());
function structuredValue(text){try{const value=JSON.parse(text);return value&&typeof value==='object'?value:undefined;}catch{return undefined;}}
function renderStructured(value,depth=0){
 const out=node('section','answer-structured');
 if(depth>20){out.append(node('p','','Additional nested detail is available in the copied or downloaded answer.'));return out;}
 if(Array.isArray(value)){
  if(!value.length){out.append(node('p','muted','No items supplied.'));return out;}
  const keys=[...new Set(value.flatMap(item=>item&&typeof item==='object'&&!Array.isArray(item)?Object.keys(item):[]))];
  if(keys.length&&keys.length<=8&&value.every(item=>item&&typeof item==='object'&&!Array.isArray(item)&&Object.values(item).every(v=>v===null||typeof v!=='object'))){
   const wrap=node('div','answer-table'),table=node('table'),head=node('thead'),row=node('tr');for(const key of keys)row.append(node('th','',readableLabel(key)));head.append(row);table.append(head);const body=node('tbody');for(const item of value){const row=node('tr');for(const key of keys){const cell=node('td');cell.append(renderStructured(item[key],depth+1));row.append(cell);}body.append(row);}table.append(body);wrap.append(table);out.append(wrap);
  }else{const list=node('ol','answer-items');for(const item of value){const entry=node('li');entry.append(renderStructured(item,depth+1));list.append(entry);}out.append(list);}return out;
 }
 if(value&&typeof value==='object'){
  const details=node('dl','answer-values');for(const [key,item] of Object.entries(value)){const entry=node('div');entry.append(node('dt','',readableLabel(key)));const content=node('dd');content.append(renderStructured(item,depth+1));entry.append(content);details.append(entry);}out.append(details);if(!Object.keys(value).length)out.append(node('p','muted','No details supplied.'));return out;
 }
 if(typeof value==='string'){
  if(value===''){out.append(node('p','','Empty string'));return out;}
  const parsed=structuredValue(value.trim().replace(/^\x60\x60\x60(?:json)?\s*\n?([\s\S]*?)\n?\x60\x60\x60$/i,'$1'));
  if(parsed!==undefined)return renderStructured(parsed,depth+1);
  if(/\x60\x60\x60json/i.test(value))return renderAnswer(value);
 }
 const formatted=value===null||value===undefined?'Not supplied':typeof value==='boolean'?(value?'Yes':'No'):String(value);for(const line of formatted.split('\n')){if(!line.trim())continue;const p=node('p');inline(p,line);out.append(p);}return out;
}
export function renderAnswer(text){
 text=String(text??'');const out=node('div','answer-markdown'),whole=structuredValue(text.trim());if(whole!==undefined){out.append(renderStructured(whole));return out;}
 const lines=text.replace(/\r\n/g,'\n').split('\n');let i=0;
 const cells=line=>line.trim().replace(/^\||\|$/g,'').split('|').map(x=>x.trim());
 while(i<lines.length){let line=lines[i];if(!line.trim()){i++;continue;}
  if(/^```/.test(line.trim())){const language=line.trim().slice(3).trim().toLowerCase(),code=[];i++;while(i<lines.length&&!/^```/.test(lines[i].trim()))code.push(lines[i++]);i++;const content=code.join('\n'),value=['json',''].includes(language)?structuredValue(content):undefined;if(value!==undefined)out.append(renderStructured(value));else if(language==='json'){out.append(node('p','assistant-error','This structured section could not be read. Try generating the answer again.'));}else{const pre=node('pre');pre.append(node('code','',content));out.append(pre);}continue;}
  if(i+1<lines.length&&line.includes('|')&&cells(lines[i+1]).every(x=>/^:?-{3,}:?$/.test(x))){const wrap=node('div','answer-table'),table=node('table'),head=node('thead'),header=node('tr');for(const cell of cells(line)){const th=node('th');inline(th,cell);header.append(th);}head.append(header);table.append(head);i+=2;const body=node('tbody');while(i<lines.length&&lines[i].includes('|')&&lines[i].trim()){const row=node('tr');for(const cell of cells(lines[i++])){const td=node('td');inline(td,cell);row.append(td);}body.append(row);}table.append(body);wrap.append(table);out.append(wrap);continue;}
  const h=line.match(/^(#{1,6})\s+(.+)/);if(h){const heading=node('h'+Math.min(4,h[1].length+1));inline(heading,h[2]);out.append(heading);i++;continue;}
  if(/^\s*([-*+] |\d+[.)] )/.test(line)){const ordered=/^\s*\d+[.)] /.test(line),list=node(ordered?'ol':'ul');while(i<lines.length&&(ordered?/^\s*\d+[.)] /:/^\s*[-*+] /).test(lines[i])){const item=node('li');inline(item,lines[i++].replace(/^\s*(?:[-*+]|\d+[.)])\s+/,''));list.append(item);}out.append(list);continue;}
  const paragraph=node(line.startsWith('> ')?'blockquote':'p');inline(paragraph,line.replace(/^> /,''));out.append(paragraph);i++;
 }
 return out;
}
export function readableAnswer(text){
 const read=element=>{
  if(element.nodeType===3)return element.textContent;
  if(element.tagName==='TABLE')return [...element.children].flatMap(section=>[...section.children]).map(row=>[...row.children].map(cell=>[...cell.childNodes].map(read).join('').trim()).join(' | ')).join('\n')+'\n\n';
  if(element.tagName==='DT')return element.textContent+':\n';
  if(element.tagName==='LI')return '- '+[...element.childNodes].map(read).join('').trim()+'\n';
  const content=[...element.childNodes].map(read).join('');
  return /^(P|H[1-6]|PRE|BLOCKQUOTE|DD)$/.test(element.tagName)?content.trim()+'\n\n':content;
 };
 return read(renderAnswer(text)).replace(/\n{3,}/g,'\n\n').trim();
}
