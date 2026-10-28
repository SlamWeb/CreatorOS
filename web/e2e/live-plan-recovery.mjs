// Real DeepSeek recovery from a deliberately failed plan in isolated SQLite.
import { chromium } from '@playwright/test';
import { writeFileSync } from 'node:fs';
const [base, reportPath] = process.argv.slice(2);
if (!base || !reportPath) throw new Error('Expected isolated base and report path');
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage();
try {
  await page.goto(`${base}/agent?creator=stages-live`);
  await page.getByText('账号：读取中…').waitFor({state:'hidden',timeout:30000});
  await page.getByRole('textbox',{name:'给 Agent 的消息'}).fill('请处理接下来后台测试计划的结果：如果读取 Skill 失败且确认无副作用，请安全跳过失败项，调整后续步骤改查账号 BGM 材料，然后继续并汇报。不要生图、不要改选题或 Skill。');
  const responsePromise=page.waitForResponse(r=>r.url().endsWith('/turns') && r.request().method()==='POST');
  await page.getByRole('button',{name:/发送/}).click();
  const response=await responsePromise;
  if(!response.ok()) throw new Error(await response.text());
  const sid=(await response.json()).id;
  for(let i=0;i<120;i++) {
    const view=await (await page.request.get(`${base}/api/agent/sessions/${sid}`)).json();
    if(view.status!=='running') break;
    await page.waitForTimeout(1000);
  }
  const path=`${base}/api/agent/sessions/${sid}/plans`;
  const created=await page.request.post(path,{data:{request_id:`recovery-${Date.now()}`,title:'失败后调整的隔离测试',steps:[
    {id:'missing',title:'读取不存在的 Skill',tool:'get_producer_skill',arguments:{skill_id:'missing-skill--0123456789abcdef'}},
    {id:'next',title:'等待读取后查材料',tool:'manage_material',arguments:{action:'list'},depends_on:['missing']}
  ]}});
  if(!created.ok()) throw new Error(await created.text());
  const id=(await created.json()).id;
  for(let i=0;i<180;i++) {
    const view=await (await page.request.get(`${base}/api/agent/sessions/${sid}`)).json();
    const plan=await (await page.request.get(`${path}/${id}`)).json();
    if(view.status!=='running' && plan.continuation_state==='done') {
      writeFileSync(reportPath,JSON.stringify({session:view,plan},null,2));
      const missing=plan.steps.find(s=>s.id==='missing');
      const bgm=plan.steps.find(s=>s.tool==='manage_material' && s.status==='succeeded');
      if(missing.status!=='skipped' || !bgm || plan.status!=='completed') throw new Error('Recovery did not complete; report retained');
      console.log('live_failure_recovery=passed'); break;
    }
    if(i===179) throw new Error('Recovery timed out');
    await page.waitForTimeout(1000);
  }
} finally { await browser.close(); }
