// Opt-in paid probe: real Codex, isolated DB and previously generated images.
import { chromium } from '@playwright/test';
import { writeFileSync } from 'node:fs';
const [base, reportPath] = process.argv.slice(2);
if (!base || !reportPath) throw new Error('Pass isolated server URL and report path');
const share='2.89 复制打开抖音，看看【傲慢无罪🐾的作品】好兄弟天天开心，有福同享有难你当 # 接福 # 兄... https://v.douyin.com/l5keSl2qzb8/ 07/01 :4pm C@H.ic kPx:/';
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
try {
  await page.goto(`${base}/?creator=stages-live&view=materials&collection=c5ef3aac-d0f5-4222-b3fd-9adcd63a43e2`);
  await page.getByRole('button',{name:'制作视频',exact:true}).click();
  const form=page.getByRole('form',{name:'制作视频'});
  await form.getByLabel('视频所属选题').selectOption('stages-topic');
  await form.getByLabel('视频任务名称').fill('分享链接直接剪辑测试');
  await form.getByLabel('剪辑要求',{exact:true}).fill('用现有两张图、用户给的抖音 BGM 做六秒竖版短视频；请遵照 Skill，自行读取分享链接。');
  await form.getByRole('checkbox',{name:/image-beat-story/}).first().check();
  await form.getByLabel('BGM 分享链接').fill(share);
  let parsed=false;
  page.on('request',r=>{if(r.method()==='POST' && r.url().includes('/materials')) parsed=true;});
  const post=page.waitForResponse(r=>r.request().method()==='POST' && r.url().includes('/production-tasks'));
  await form.getByRole('button',{name:'提交视频制作'}).click();
  const response=await post;
  if(!response.ok()) throw new Error(await response.text());
  const submitted=await response.json();
  if(parsed) throw new Error('UI attempted to pre-parse the BGM');
  const item=submitted.items[0];
  console.log(`run=${item.run_id}`);
  for(let i=0;i<1200;i++) {
    const run=await (await page.request.get(`${base}/api/runs/${item.run_id}`)).json();
    if(!['queued','producing','validating'].includes(run.status)) {
      writeFileSync(reportPath,JSON.stringify({share,run,submitted},null,2));
      console.log(`run_status=${run.status} error=${run.error_message || ''}`);
      break;
    }
    if(i===1199) throw new Error('Codex video timed out');
    await page.waitForTimeout(1000);
  }
} finally { await browser.close(); }
