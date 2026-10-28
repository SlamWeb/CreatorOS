// Opt-in: real DeepSeek and browser against an explicitly isolated server.
import { chromium } from '@playwright/test';
import { writeFileSync } from 'node:fs';
const [base, reportPath, mode] = process.argv.slice(2);
if (!base || !reportPath) throw new Error('Expected isolated base URL and report path');
const browser = await chromium.launch({channel:'chrome', headless:true});
const page = await browser.newPage();
try {
  await page.goto(`${base}/agent?creator=stages-live`);
  await page.getByText('账号：读取中…').waitFor({state:'hidden', timeout:30000});
  const text = mode === 'failure'
    ? '请用后台计划处理测试任务：先读取名为 missing-skill-for-recovery-test 的 Skill，如果读取失败请你自行分析并安全跳过失败步骤，调整后续步骤为查询账号已有 BGM 材料，然后继续计划并汇报。不要生图、不创建或删除选题，也不要修改 Skill。'
    : '请用后台计划处理测试任务：先查看「一天的世界」栏目现有选题，然后设置一个需要你判断的检查点，判断后把尚未执行的下一步改为查看账号已有的 BGM 材料，再自行继续计划并向我汇报。无需问我，不要生图、不创建或删除选题，也不要修改 Skill。';
  await page.getByRole('textbox',{name:'给 Agent 的消息'}).fill(text);
  const responsePromise = page.waitForResponse(r => r.url().endsWith('/turns') && r.request().method()==='POST');
  await page.getByRole('button',{name:/发送/}).click();
  const response = await responsePromise;
  if (!response.ok()) throw new Error(await response.text());
  const sid = (await response.json()).id;
  let observedCheckpoint = false;
  let last = '';
  for(let i=0;i<300;i++) {
    const view=await (await page.request.get(`${base}/api/agent/sessions/${sid}`)).json();
    const plans=(await (await page.request.get(`${base}/api/agent/sessions/${sid}/plans`)).json()).items;
    if(plans.length) {
      const plan=await (await page.request.get(`${base}/api/agent/sessions/${sid}/plans/${plans[0].id}`)).json();
      if(plan.steps.some(s=>mode === 'failure' ? ['failed','skipped'].includes(s.status) : s.tool==='request_review' && s.status==='succeeded')) observedCheckpoint=true;
      const state=JSON.stringify({chat:view.status, plan:plan.status, steps:plan.steps.map(s=>`${s.id}:${s.status}`)});
      if(state!==last) { console.log(state); last=state; }
      if(mode === 'failure' && !observedCheckpoint && plan.status === 'completed' && view.status !== 'running') {
        writeFileSync(reportPath,JSON.stringify({session:view,plan},null,2));
        throw new Error('Model avoided the intended failure; recovery was not exercised');
      }
      if(observedCheckpoint && ['completed','needs_attention'].includes(plan.status) && view.status!=='running' && plan.continuation_state==='done') {
        writeFileSync(reportPath,JSON.stringify({session:view,plan},null,2));
        if(plan.status!=='completed') throw new Error('Plan ended needing attention; report retained');
        if(!plan.steps.some(s=>s.tool==='manage_material' && s.status==='succeeded')) throw new Error('No successful BGM material lookup');
        console.log('live_review=passed'); break;
      }
    }
    if(i===299) throw new Error('Timed out before checkpoint/continuation completed');
    await page.waitForTimeout(1000);
  }
} finally { await browser.close(); }
