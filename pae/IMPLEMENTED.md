# PAE 实现状态（capabilities.json 的人类可读快照；生成脚本未随本仓库发布）

> 更新方式：对照 pae/capabilities.json 手工同步（权威数据源始终是它）
> 用途：把「纸上规格」与「线上行为」分开列——防止再出现「蓝图 6 项未启动但没人知道」。
> 判断某能力是否可用以 GET /v1/cap/manifest 为准（同一数据源）。

## 汇总

| 项 | 数 |
|---|---|
| 能力总数 | 42 |
| 已实现 | 41 |
| 有壳未实现（stub，如实回答） | 1 |
| 规划中（只有设计） | 0 |

## 已实现（41）

| 能力 | 类型 | scope | HTTP | 说明 |
|---|---|---|---|---|
| `pae.health` | observe | read | GET /v1/health | 引擎存活检查 |
| `pae.annotate` | observe | read | POST /v1/annotate | 给一段文本返回注解（含记账：写入 encounter 事件） |
| `pae.status` | observe | read | GET /v1/status | 学习状态总览（事件数/词表规模/高频掌握词） |
| `pae.config` | observe | read | GET /v1/config | 当前生效配置（只读） |
| `pae.event` | inform | write | POST /v1/event | 上报观察/交互事件（annotation_shown / hover / known_click）——三证据的发射口 |
| `pae.persona` | observe | read | GET /v1/persona | 读取人格参数与渲染后的提示词——外部智能体扮演同一角色所必需 |
| `pae.persona.preview` | observe | read | POST /v1/persona/preview | 用给定参数渲染提示词（不落盘） |
| `pae.persona.update` | act | write | POST /v1/persona | 保存人格参数（本机面板用；外部 actor 走同一守门） |
| `pae.persona.say` | act | write | POST /v1/persona/say | 用当前人格说一句（面板试说/主动推送都走这里） |
| `pae.context` | observe | read | POST /v1/cap/pae.context | 完整上下文装配（L0–L6；1M 策略=默认全量装载，超限按固定顺序渐进裁剪） |
| `pae.word` | observe | read | POST /v1/cap/pae.word | 单词状态（S 值/遭遇次数/释义/是否在带内） |
| `pae.trend` | observe | read |   | 掌握度趋势 |
| `pae.events` | observe | read | POST /v1/cap/pae.events | 原始事件查询 |
| `pae.decisions` | observe | read | GET /v1/cap/pae.decisions | 决策日志：谁（哪个 actor）因为什么改了哪个变量、改前改后、门控结果与效果 |
| `pae.page` | observe | read | POST /v1/cap/pae.page | 页面现场（当前 URL/已注解词/选区） |
| `pae.metrics` | observe | read | POST /v1/cap/pae.metrics | 引擎表现指标（曝光/悬停/点击/预算利用率/被忽略 Top 词）——反馈闭环的输入 |
| `pae.memory.search` | observe | read |   | 长期记忆检索（facts 表） |
| `pae.memory.recent` | observe | read |   | 最近对话（关引擎重开仍可取回） |
| `pae.propose` | act | write | POST /v1/cap/pae.propose | 参数变更提案（JSON Patch 方言 + 依据清单；走三层守门，可回滚） |
| `pae.pin` | act | write |   | 标记「认识了」（写 user_pin/known_click 事件） |
| `pae.remember` | act | write |   | 写一条长期记忆（必须带 evidence，无证据不收） |
| `pae.forget` | act | write |   | 撤销一条记忆（superseded_by 语义，不物理删除） |
| `pae.say` | act | write |   | 往页面推一条角色消息（受 proactivity 与每日上限约束） |
| `pae.hint` | act | write |   | 在页面上强化/弱化某词（不改 S，只改本次渲染） |
| `pae.session.open` | session | write |   | 开一个会话，返回 session_id |
| `pae.session.turn` | session | write |   | 追加一轮对话 |
| `pae.session.close` | session | write |   | 结束会话（可选摘要） |
| `pae.push.log` | observe | read | GET /v1/push/log | 角色对我说过什么（推送审计：含被每日上限拦下的） |
| `pae.params` | observe | read | GET /v1/cap/pae.params | 当前变量：覆盖层 + 有效值 + 默认值 + 取值范围（AI 改了什么都看得见） |
| `pae.agent.tick` | act | write | POST /v1/cap/pae.agent.tick | 让内置角色跑一轮：观察状态 → LLM 判断 → 通过能力面行动 → 全程留痕（agent_log 可读它的 thought）。这是「AI 智能元素」的循环本体 |
| `pae.agent.log` | observe | read | GET /v1/cap/pae.agent.log | 它想过什么、做了什么（透明：你能看见 AI 的判断过程） |
| `pae.audit` | observe | read | GET /v1/cap/pae.audit | 调用审计：谁调用了哪项能力、成功与否（含被拒的尝试）——「哪个智能体在动我的系统」 |
| `pae.permissions` | observe | read | GET /v1/cap/pae.permissions | 权限矩阵：每个 actor 能做什么、限制是什么（校准结果摆出来，用户可查） |
| `pae.agent.schedule` | observe | read | GET /v1/cap/pae.agent.schedule | AI 主动巡检的调度状态（是否开启、上次何时跑、今天几次、下次该不该跑） |
| `pae.agent.schedule.set` | act | write | POST /v1/cap/pae.agent.schedule.set | 开启/关闭 AI 主动巡检（**只有用户能开**；AI 不能给自己开） |
| `pae.key.issue` | act | write | POST /v1/cap/pae.key.issue | 签发外部智能体的 key（**只有用户能签发**；明文只返回一次） |
| `pae.outcomes` | observe | read | POST /v1/cap/pae.outcomes | **结果侧指标（尺子）**：至少 eval_days 天前被注解过的词里，后来真的点过「认识了」的比例；并回填每次参数变更的前后效果 |
| `pae.familiarity` | observe | read | POST /v1/cap/pae.familiarity | 熟悉度投影（35号）：谁会被「无视即熟悉」豁免、为什么；默认 shadow 模式（只记录不生效） |
| `pae.familiarity.restore` | act | write | POST /v1/cap/pae.familiarity.restore | 一键恢复：让某个词重新被注解（写 revoke 覆盖 + 30 天冷却，尊重人工信号） |
| `pae.sensejudge` | act | write | POST /v1/cap/pae.sensejudge | 义项判定（P5 影子期）：对给定词+句子跑一次 LLM 义项判定，**只写缓存与决策日志，不改上屏内容** |
| `pae.mute` | act | write | POST /v1/cap/pae.mute | 静音开关（读写）：静音时引擎侧主动说话（规则版）与推送一并安静；扩展「静音」按钮经此同步 |

## 规划中（只有设计，调用返回 501）（0）

| 能力 | 类型 | 说明 |
|---|---|---|

## 传输层

| 传输 | 状态 | 说明 |
|---|---|---|
| http | implemented |  |
| mcp | implemented | 由 mcp_server.py 提供（stdio）；tools/list 返回同一份清单（实测 41 工具，t18_mcp.json PASS） |
| skill | implemented | 见 skills/pae-bridge/SKILL.md（能力面的人类可读快照，t20_character.json 场景已实测） |

## 鉴权

- 头：X-PAE-Key。不带 = local（本机扩展，向后兼容）；带了必须匹配 keys.json，否则 401
- 签发：python -m pae_core.keys issue <actor>
