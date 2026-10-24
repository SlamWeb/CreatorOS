# 后台等待、DS拟题与栏目改名

## 真实问题与修复边界

- 原会话 `dc932608-3961-40ca-8575-a86a5eea926b`，用户请求 `129e1536-8ec4-4582-bfab-82949bda25b9`：只提炼一次，但get_skill_extraction调用27次。旧job `66b79ea2c049a18f705af779b0148ca82da275792ff243d22053ba989a3fb4bd` 已saved，不重新提炼、不改旧Trace。
- Web宿主短时观察同一个提炼/改稿：2秒GET、最长60秒，状态和公开活动合并到原工具行，不反复花模型轮次。超时、断连或对话停止只代表观察unknown，不取消原任务，也不重提。
- 每条人类消息内，同任务只允许一次真实状态GET；重复请求复用可信回执，完整工具批次配对落盘后结束本轮，不能破坏tool-call/result对应关系。新消息、不同任务、终态或成功控制操作不受旧额度影响。ready但operation仍在改稿也算活跃。
- RSI仍由既有持久worker自己循环，返回后台阶段后结束聊天，不另行提交生产。后台等待超过观察窗口后不承诺Agent自己醒来发消息；用户可从任务入口看结果，或后续问一次。
- 普通拟题默认DS直接完成；明确联网、时效或外部证据需求才委派Codex。没有删联网能力，也不把知识建议冒充联网调研。

## 验证与证据

- 新循环测试先复现：同批次两次状态查询仍执行，累计打满上限；原始进度反复送模型；宿主不合并进度。修复后通过。受控HTTP用于等待/超时/断连/错误注入，不是Codex产物质量证明。
- 联合 `test_series_rename / test_media_collection_agent / test_media_collections / test_host_tool_contract / test_background_agent / test_pending_tasks / test_background_wait / test_skill_iteration_agent / test_chat_stream`：72/72，100.828秒。
- 再发现ready草稿正在revise时漏限查，先新增红测再修；受影响的七模块定向52/52，31.801秒。已有RSI测试的Thread最后一个位置断言过时，改为按真实函数签名核对iteration_mode，不放宽业务断言。
- 原前端真实DeepSeek2/2，37.5秒；另加只读表单检查1/1，12.6秒。根目录误执行Vite入口失败，改为web目录构建通过；TypeScript通过，机械检测无发现。

| 真实前端操作 | 工具 / 请求 | 用量 | 实际结果 |
| --- | --- | --- | --- |
| “帮我给英语词汇栏目调研5组高考考研常用的同义词选题，先给我建议” | 0工具，1次DS请求 | 输入15348，输出1367，缓存命中2560 | 给出5组建议，没委派Codex、没入队或生产 |
| Web设置改名、刷新，再让账号Agent改名 | Web PATCH；Agent一次update_series_composition，2次DS请求 | 输入31031，输出175，缓存命中29952 | 同栏目ID由“栏目改名测试”→“英语词汇实验室”→“英语场景词汇”，刷新保留；工具详情可展开 |

- 环境：新SQLite副本 `tmp/background-rename-live-20261024-a/isolated.sqlite`，端口8895，后台消费者关闭；只额外允许新造测试栏目PATCH。没有修改正式栏目或触发生产。
- DS报告：同目录 `topics-result.json / rename-result.json`；会话分别为 `1de56d3f-9e7e-4060-a846-9c705278454c / 7a82c9ec-c9d7-4b30-996e-871a88cbb9fe`。
- 浏览器证据：`tmp/background-rename-20261024/browser-a` 和 `browser-form`。1440×900及390×844截图已查看，实际表单可读、无横溢出，原配色/Logo不变；未宣称全站视觉或内容质量提升。DS建议仍有附带Skill不可用说明，不作为简洁程度或英语内容准确性的人工评分。
- 两条源Run及原图片摘要均保持不变，测试服务器没启动生产/提炼/RSI消费者。本轮没有新开真实Codex提炼或RSI，只有真实DS链路与受控任务回执测试。

## 正式启动与未载入的小补丁

- 用户要求启动；原8765无监听。SQLite先备份至 `data/creatoros.db.startup-20261024-200825.bak`，启动正常升级0018/0019。
- 第一次被旧执行标记阻止：claimed，host_pid27724，确认进程及直属子进程已退出后，将原件归档为 `data/creatoros.db.execution.archived-20261024-2013.json`，没有删除执行证据。正常启动将旧未结束生产标中断，不重画。
- 正式服务PID24864，健康接口HTTP200，前端index-B_clukmT.js。主要等待/提示词/改名改动已载入。
- 随后用户前端PATCH改名200，并POST原Run `79b8aa0d-f464-463f-9aa1-b18c368118b0/execute` 得202。该执行由前端请求触发，不是验收脚本重跑。
- 追加的ready+operation改稿限查边界代码与测试已完成，但正式进程已加载早一版模块；再次维护begin返回409，复查为活动生产。未强停、未清理新执行记录；该补丁待下一次空闲重启载入。没有调整旧RSI标准、分数或图片。
