// Throwaway design exploration: all examples and classifications are illustrative and memory-only.
const categories = {
  content: { name: '内容创作', icon: '<circle cx="12" cy="12" r="8"/><path d="M8 11h8M8 15h5"/>' },
  visual: { name: '视觉表现', icon: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="m7 16 4-4 3 3 2-2 2 3"/>' },
  process: { name: '素材处理', icon: '<path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4"/>' },
  rule: { name: '规则约束', icon: '<path d="M12 3 20 7v10l-8 4-8-4V7z"/><path d="m8.5 12 2.2 2.2 4.8-5"/>' },
  other: { name: '待分类', icon: '<circle cx="12" cy="12" r="8" stroke-dasharray="2 3"/><path d="M12 8v4m0 4h.01"/>' }
};
const skills = [
  {id:'story', name:'旁白与分镜', tags:['content'], description:'从主题到旁白，再整理为分镜脚本。'},
  {id:'outline', name:'知识故事线', tags:['content'], description:'组织论点、叙事节奏与内容结构。'},
  {id:'english', name:'莓说英语', tags:['visual'], description:'四格学习卡的角色与画面风格。'},
  {id:'pixel', name:'像素世界', tags:['visual'], description:'固定角色形象与像素视觉风格。'},
  {id:'bgm', name:'BGM Understanding', tags:['process'], description:'分析音乐拍点和段落变化。'},
  {id:'beat', name:'图片音乐卡点', tags:['process'], description:'按音乐事件编排图片与镜头。'},
  {id:'fixed', name:'固定主体规则', tags:['rule','visual'], description:'约束主体的构图与视觉一致性。'},
  {id:'tone', name:'内容准确性规则', tags:['rule','content'], description:'约束信息核对与表达方式。'},
  {id:'unknown', name:'未分类 Skill', tags:['other'], description:'尚未确认用途的演示素材。'}
];
const columns = ['小莓say', '坚持', '新栏目（示意）'];
const qs = new URLSearchParams(location.search);
let variant = ['a','b','c'].includes(qs.get('variant')) ? qs.get('variant') : 'a';
let filter = categories[qs.get('type')] ? qs.get('type') : 'all';
let query = qs.get('q') || '';
let activeColumn = 0;
let selection = columns.map(() => new Set());
const main = document.querySelector('#main');
const dialog = document.querySelector('#detail');
function icon(type) { return `<span class="icon ${type}" aria-hidden="true"><svg viewBox="0 0 24 24">${categories[type].icon}</svg></span>`; }
function tags(skill) { return `<span class="badge-list">${skill.tags.map(t=>`<span class="badge ${t}">${categories[t].name}</span>`).join('')}</span>`; }
function nameButton(skill) { return `<button class="item-open item-name" data-detail="${skill.id}">${skill.name}</button>`; }
function addButton(skill, index=activeColumn) { const active=selection[index].has(skill.id); return `<button class="add ${active?'on':''}" data-toggle="${skill.id}" data-column="${index}" aria-label="${active?'移除':'添加'} ${skill.name}" aria-pressed="${active}">${active?'✓':'+'}</button>`; }
function filtered() { const q=query.trim().toLowerCase(); return skills.filter(s => (filter==='all'||s.tags.includes(filter)) && (!q || (s.name+' '+s.description).toLowerCase().includes(q))); }
function chosen(index=activeColumn) { const values=skills.filter(s=>selection[index].has(s.id)); return values.length ? values.map(s=>`<span class="chosen">${icon(s.tags[0])}<b>${s.name}</b><button data-remove="${s.id}" data-column="${index}" aria-label="移除 ${s.name}">×</button></span>`).join('') : '<span class="empty">拖入或点击＋，添加 Skill</span>'; }
function head(subtitle) { return `<header class="head"><div><h1>Skill 库</h1><p class="subtitle">${subtitle}</p></div><span class="demo">设计实验 · 示例数据，不会保存</span></header><div class="toolrow"><button class="button" data-demo="从作品提炼">从作品提炼</button><button class="button" data-demo="自己写 Skill">自己写 Skill</button><button class="button" data-demo="安装 Skill">安装 Skill</button></div>`; }
function filterControls() { return `<div class="legend" aria-label="按类别筛选"><button class="pill ${filter==='all'?'active':''}" data-filter="all" aria-pressed="${filter==='all'}">全部</button>${Object.entries(categories).map(([k,c])=>`<button class="pill ${filter===k?'active':''}" data-filter="${k}" aria-pressed="${filter===k}">${c.name}</button>`).join('')}</div>`; }
function searchBox() { const safe=query.replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;'); return `<input class="search" name="skill-search" type="search" aria-label="搜索 Skill" placeholder="搜索名称或描述…" value="${safe}" autocomplete="off">`; }
function headerTools() { return `<div class="toolrow">${searchBox()}${filterControls()}</div>`; }
function card(skill) { return `<article class="shelf-card ${skill.tags[0]}" draggable="true" data-drag="${skill.id}"><div class="cardtop">${icon(skill.tags[0])}${addButton(skill)}</div>${nameButton(skill)}<p class="item-summary">${skill.description}</p>${tags(skill)}</article>`; }
function ribbon(skill) { return `<article class="source-chip" draggable="true" data-drag="${skill.id}">${icon(skill.tags[0])}<div>${nameButton(skill)}${tags(skill)}</div>${addButton(skill)}</article>`; }
function renderA() {
  const list=filtered();
  return `${head('按用途探索可复用的创作素材')}${headerTools()}<div class="shelf-intro"><h2>素材展台</h2><span class="caption">多标签 Skill 会出现在多个分类中</span></div><div id="results">${Object.entries(categories).map(([key,c])=>{let group=list.filter(s=>s.tags.includes(key));return group.length?`<section class="shelf"><div class="shelf-head">${icon(key)}<h2>${c.name}</h2><span class="count">${group.length} 项示例</span></div><div class="shelf-row" tabindex="0" aria-label="${c.name}素材，横向滚动">${group.map(card).join('')}</div></section>`:''}).join('')||'<p class="empty">没有匹配的 Skill</p>'}</div><section class="tray" data-drop="0" aria-label="栏目素材托盘"><strong>栏目素材 · ${selection[0].size}</strong><div class="selected-list" aria-live="polite">${chosen(0)}</div><div class="tray-actions"><button class="fake" data-demo="保存到栏目">保存到栏目</button><button class="fake" data-demo="融合成新 Skill">融合</button></div></section>`;
}
function renderB() {
 const list=filtered();
 return `${head('把需要的素材带到同一张创作台') }<div class="canvas-layout"><section class="canvas" data-drop="0" aria-label="栏目创作台"><div class="canvas-top"><div><h2 class="canvas-title">小莓say <span class="caption">· 示例栏目</span></h2><p class="subtitle">素材之间没有预设顺序</p></div><span class="caption">${selection[0].size} 项 · 未保存</span></div><div class="canvas-field" aria-live="polite">${selection[0].size?chosen(0):'<div class="canvas-empty empty">把 Skill 拖到这里<br>或通过下方 ＋ 添加</div>'}</div></section><aside class="side-info"><h2>当前组合</h2><p class="note">这里是示意画布。添加只改变本地草稿，不代表绑定成功。</p><button class="button primary" data-demo="保存到栏目">保存到栏目</button><button class="button" data-demo="融合成新 Skill">融合成新 Skill</button></aside></div><section class="source-band"><div class="shelf-intro"><h2>素材抽屉</h2><span class="caption">拖动素材或点击＋</span></div>${headerTools()}<div id="results" class="source-cards" tabindex="0" aria-label="素材抽屉，横向滚动">${list.map(ribbon).join('')||'<p class="empty">没有匹配的 Skill</p>'}</div></section>`;
}
function renderC() {
 const list=filtered();
 return `${head('比较不同栏目各自使用的 Skill')}${headerTools()}<div class="shelf-intro"><h2>栏目配置矩阵</h2><span class="caption">点击交叉格加入或移除；也可拖到栏目列</span></div><div class="matrix-wrap" id="results" tabindex="0" role="region" aria-label="栏目配置矩阵，可横向滚动"><table class="matrix"><thead><tr><th scope="col">Skill</th>${columns.map((c,i)=>`<th scope="col" data-drop="${i}">${c}<br><span class="caption">${selection[i].size} 项 · 未保存</span></th>`).join('')}</tr></thead><tbody>${list.map(s=>`<tr draggable="true" data-drag="${s.id}"><th scope="row"><div class="matrix-skill">${icon(s.tags[0])}<div>${nameButton(s)}${tags(s)}</div></div></th>${columns.map((c,i)=>`<td data-drop="${i}"><button class="cell ${selection[i].has(s.id)?'on':''}" data-toggle="${s.id}" data-column="${i}" aria-pressed="${selection[i].has(s.id)}" aria-label="${c}：${selection[i].has(s.id)?'移除':'加入'} ${s.name}">${selection[i].has(s.id)?'✓':'+'}</button></td>`).join('')}</tr>`).join('')}</tbody></table>${list.length?'':'<p class="empty">没有匹配的 Skill</p>'}</div><div class="matrix-footer"><strong>${selection.reduce((n,s)=>n+s.size,0)} 项待保存</strong><span class="note">示例中的三个栏目仅供比较，均不会写入。</span><button class="button primary" data-demo="保存栏目配置">保存配置</button><button class="button" data-demo="融合成新 Skill">融合 Skill</button></div>`;
}
function updateUrl() { const u=new URL(location.href);u.searchParams.set('variant',variant); if(filter==='all')u.searchParams.delete('type');else u.searchParams.set('type',filter); if(query)u.searchParams.set('q',query);else u.searchParams.delete('q');history.replaceState({},'',u); }
function render(restore) { main.innerHTML=({a:renderA,b:renderB,c:renderC})[variant]();document.querySelectorAll('[data-variant]').forEach(b=>{b.classList.toggle('active',b.dataset.variant===variant);b.setAttribute('aria-current',b.dataset.variant===variant?'page':'false')}); bind(); if(restore){let el=main.querySelector(restore);if(el)el.focus()} }
function toggle(id,index) {const set=selection[index];set.has(id)?set.delete(id):set.add(id);render(`[data-toggle="${id}"][data-column="${index}"]`);}
function bind() {
 const search=main.querySelector('.search'); let composing=false;
 search.addEventListener('compositionstart',()=>composing=true);
 search.addEventListener('compositionend',e=>{composing=false;query=e.target.value;updateUrl();const pos=e.target.selectionStart;render();const input=main.querySelector('.search');input.focus();input.setSelectionRange(pos,pos)});
 search.addEventListener('input',e=>{if(composing||e.isComposing)return;query=e.target.value;updateUrl();const pos=e.target.selectionStart;render();const input=main.querySelector('.search');input.focus();input.setSelectionRange(pos,pos)});
 main.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;updateUrl();render(`[data-filter="${filter}"]`)});
 main.querySelectorAll('[data-toggle]').forEach(b=>b.onclick=()=>toggle(b.dataset.toggle,Number(b.dataset.column)));
 main.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{let id=b.dataset.remove,i=Number(b.dataset.column);selection[i].delete(id);render();(main.querySelector(`[data-toggle="${id}"][data-column="${i}"]`) || main.querySelector(".search"))?.focus()});
 main.querySelectorAll('[data-detail]').forEach(b=>b.onclick=()=>{let s=skills.find(x=>x.id===b.dataset.detail);dialog.querySelector('h2').textContent=s.name;dialog.querySelector('p').textContent=s.description;dialog.showModal()});
 main.querySelectorAll('[data-demo]').forEach(b=>b.onclick=()=>{dialog.querySelector('h2').textContent=b.dataset.demo;dialog.querySelector('p').textContent='设计实验：此按钮不会保存、修改栏目或调用模型。';dialog.showModal()});
 main.querySelectorAll('[data-drag]').forEach(el=>{el.ondragstart=e=>{e.dataTransfer.setData('text/plain',el.dataset.drag);e.dataTransfer.effectAllowed='copy';el.classList.add('dragging')};el.ondragend=()=>el.classList.remove('dragging')});
 main.querySelectorAll('[data-drop]').forEach(zone=>{zone.ondragover=e=>{e.preventDefault();zone.classList.add('over')};zone.ondragleave=()=>zone.classList.remove('over');zone.ondrop=e=>{e.preventDefault();e.stopPropagation();zone.classList.remove('over');const id=e.dataTransfer.getData('text/plain'),i=Number(zone.dataset.drop);if(skills.some(s=>s.id===id)){selection[i].add(id);render(`[data-toggle="${id}"][data-column="${i}"]`)}}});
}
document.querySelectorAll('[data-variant]').forEach(b=>b.onclick=()=>{variant=b.dataset.variant;filter='all';query='';updateUrl();render();window.scrollTo(0,0)});
document.querySelector('#close-detail').onclick=()=>dialog.close();
document.addEventListener('keydown',e=>{if(dialog.open||!document.activeElement.closest('.switcher'))return;if(e.key==='ArrowRight'||e.key==='ArrowLeft'){let a=['a','b','c'];variant=a[(a.indexOf(variant)+(e.key==='ArrowRight'?1:2))%3];filter='all';query='';updateUrl();render();document.querySelector(`[data-variant="${variant}"]`)?.focus();window.scrollTo(0,0)}});
render();
