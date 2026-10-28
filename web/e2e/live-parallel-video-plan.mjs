// Opt-in paid DeepSeek probe. Server holds production: no Codex/image calls.
import { chromium } from '@playwright/test';
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
const [base, setupPath, reportPath, mode] = process.argv.slice(2);
if (!base || !setupPath || !reportPath) throw new Error('Pass isolated URL, setup and report paths');
const setup = JSON.parse(readFileSync(setupPath, 'utf8'));
if (setup.production_dispatch !== 'held') throw new Error('Production must be held for this planning probe');
const share = '2.89 复制打开抖音，看看【傲慢无罪🐾的作品】好兄弟天天开心，有福同享有难你当 # 接福 # 兄... https://v.douyin.com/l5keSl2qzb8/ 07/01 :4pm C@H.ic kPx:/';
const query = `给我建一个选题，你为什么突然开始努力？然后用像素提问skill生出25张理由图放同一个素材集，最后再打包用image beat story，BGM就用：《${share}》这个链接里的，一两s以后开始卡点。`;
const browser = await chromium.launch({channel:'chrome', headless:true});
const page = await browser.newPage({viewport:{width:1440,height:1000}});
try {
  await page.goto(`${base}/agent?creator=${setup.creator_id}&chat=${setup.session_id}`);
  if(mode !== 'observe') {
    const input = page.getByRole('textbox', {name:'给 Agent 的消息'});
    await input.fill(query);
    const responsePromise = page.waitForResponse(r => r.url().endsWith('/turns') && r.request().method()==='POST');
    await page.getByRole('button',{name:/发送/}).click();
    const response = await responsePromise;
    if (!response.ok()) throw new Error(await response.text());
  }
  let view;
  for (let i=0;i<360;i++) {
    view = await (await page.request.get(`${base}/api/agent/sessions/${setup.session_id}`)).json();
    if (view.status !== 'running') break;
    if (i===359) throw new Error('DeepSeek turn timed out');
    await page.waitForTimeout(1000);
  }
  const listed = await (await page.request.get(`${base}/api/agent/sessions/${setup.session_id}/plans`)).json();
  const plans=[];
  for (const item of listed.items) plans.push(await (await page.request.get(`${base}/api/agent/sessions/${setup.session_id}/plans/${item.id}`)).json());
  const ledger = JSON.parse(readFileSync(path.join(path.dirname(setupPath),'chats',setup.session_id,'messages.json'),'utf8'));
  const calls=ledger.flatMap(m=>m.tool_calls || []).map(c=>({name:c.name || c.function?.name, arguments:JSON.parse(c.arguments || c.function?.arguments || '{}')}));
  const imports=calls.filter(c=>c.name==='manage_material' && c.arguments.action==='import_link');
  const imageNodes=plans.flatMap(p=>p.steps.filter(s=>s.tool==='start_content_run' && s.arguments.tasks?.some(t=>(t.kind || 'image')==='image')));
  const imageTasks=imageNodes.flatMap(s=>s.arguments.tasks.filter(t=>(t.kind || 'image')==='image'));
  const topicIds=[...new Set(imageNodes.map(s=>s.arguments.topic_id))];
  let taskLists=[];
  for(let i=0;i<60;i++) {
    taskLists=[];
    for (const topic of topicIds) taskLists.push(await (await page.request.get(`${base}/api/topics/${topic}/production-tasks`)).json());
    if(taskLists.reduce((n,list)=>n+list.items.filter(t=>t.kind==='image').length,0)===25) break;
    if(i<59) await page.waitForTimeout(1000);
  }
  const storedTasks=taskLists.flatMap(list=>list.items.filter(t=>t.kind==='image'));
  const collectionIds=[...new Set(storedTasks.map(t=>t.collection_id || taskLists.find(l=>l.items.includes(t))?.collection_id).filter(Boolean))];
  const videoNodes=plans.flatMap(p=>p.steps.filter(s=>s.tool==='start_content_run' && s.arguments.tasks?.some(t=>t.kind==='video')));
  const videoTasks=videoNodes.flatMap(s=>s.arguments.tasks.filter(t=>t.kind==='video'));
  const facts={query,session:view,plans,calls,topicIds,storedTasks,collectionIds};
  writeFileSync(reportPath,JSON.stringify(facts,null,2));
  if(imports.length) throw new Error('Agent still pre-imported BGM');
  if(imageTasks.length!==25 || storedTasks.length!==25) throw new Error(`Expected 25 tasks, planned=${imageTasks.length}, stored=${storedTasks.length}`);
  if(new Set(imageTasks.map(t=>t.title)).size!==25 || imageTasks.some(t=>/(产出|生成|制作)\s*25\s*张/.test(t.brief))) throw new Error('Image tasks were not split into distinct single-frame requirements');
  if(topicIds.length!==1 || collectionIds.length!==1) throw new Error('Images were not kept under one topic/collection');
  if(storedTasks.some(t=>t.status!=='queued')) throw new Error('Held production unexpectedly started');
  if(videoTasks.length!==1 || videoTasks[0].inputs?.bgm_reference!==share) throw new Error('Video step lost the original BGM text');
  if(!videoNodes[0].depends_on?.length) throw new Error('Video has no plan dependency');
  if(imageNodes.some(s=>!s.wait_for_completion)) throw new Error('Image plan does not wait for completion');
  await page.screenshot({path:reportPath.replace(/\.json$/,'.png'),fullPage:true});
  console.log('live_parallel_video_plan=passed (real DeepSeek; Codex dispatch held)');
} finally { await browser.close(); }
