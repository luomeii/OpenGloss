# OpenGloss

**在你本来就要读的英文网页上，提供低干扰的词级辅助。**

自动标出生词，悬停看释义，熟悉后点「认识了」。你继续阅读，OpenGloss 在本机记录接触和交互，让后续标注参考你的使用历史。

无需背词表、打卡或做测验。它帮助你读下去；阅读得快、看过一次，并不代表已经记住一个词。

> 当前为 **v0.4.0-rc.3 发布候选**。Windows 自包含安装、可选更新和恢复已实现，仍需补齐全新系统、真实浏览器与长期使用验收。尚未发布正式安装资产。
>
> **English** — Local-first vocabulary assistance for English web pages: inline highlights, hover definitions and a reading history that informs future annotations. No flashcards or quizzes. AI chat and memory are optional; core reading tools work without an API key. Windows installer candidate; browser extension installed manually.

## 阅读时是什么样

![浅色主题：英文网页上的词级标注和侧栏](docs/demo-1-light.png)

*网页正文保留原有排版，生词以三档颜色提示。侧栏展示最近接触的词和交互统计。*

![暗色主题：悬停释义卡与认识了按钮](docs/demo-2-dark-hover.png)

*悬停查看释义、熟悉度参考值和「认识了」按钮；浅色与暗色主题都可使用。*

<p align="center">
  <img src="docs/demo-3-sidebar.png" alt="侧栏特写：阅读统计、最近接触的词与角色入口" width="380">
  <br>
  <em>侧栏特写：需要时再展开查看，正文阅读始终是主要操作。</em>
</p>

截图展示已有交互，界面版本号可能早于当前候选。

- **词级辅助**：标注、悬停释义、「认识了」；每内容块默认最多8词，整页最多40词。
- **逐步适应**：接触、悬停和点击形成事件记录，熟悉度由规则重放计算。S值是系统估计，不是考试成绩或真实掌握证明。
- **可选角色**：侧栏可查看近期词汇、事实卡和对话；配置模型后支持聊天及部分主动表达，不影响无模型时的基础阅读功能。
- **本地优先**：词典、学习记录和配置保存在本机，没有使用数据遥测。模型外发内容见[隐私与数据](#隐私与数据)。

## 选择安装方式

| | Windows自包含安装包 | 源码部署 |
|---|---|---|
| 适用 | 想直接使用的Windows 10/11 x64用户 | 想研究或修改代码的人 |
| Python / pip / Git | 目标电脑不需要 | 需要Python，另装API依赖 |
| 词典 | 包内已含完整公开词典 | 需单独下载或构建 |
| 默认引擎地址 | `http://127.0.0.1:4817` | `http://127.0.0.1:4815` |
| 配置与数据 | 安装目录的`data/` | 默认位于`pae/` |
| 更新 | 管理页检查正式GitHub Release，用户选择安装 | 自行更新源码，保留个人配置与数据 |

### Windows安装包

安装候选目前仅供隔离内测。正式上传后，下载入口是[GitHub Releases](https://github.com/luomeii/OpenGloss/releases)中的 **`OpenGloss-windows-x64.zip`**。仓库「Download ZIP」是源码，不是安装包；若Release只有`dict.sqlite`，说明那里尚未提供安装版。

1. 将完整ZIP解压到新文件夹，双击`Install.cmd`。
2. 默认安装至`%LOCALAPPDATA%\OpenGloss`，创建当前用户桌面快捷方式并打开管理页。首次启动需加载完整词典，请等待安装器报告就绪。
3. 在扩展管理页开启开发者模式，手动加载安装目录的`versions/当前版本/app/pae/extension`。
4. 打开普通英文网页，稍等后悬停查看释义。安装包扩展默认连接4817，管理页默认使用4818。

Chrome扩展页为`chrome://extensions`，Edge为`edge://extensions`。夸克的扩展加载和页面兼容性尚未通过独立验收，暂不承诺可用。

程序在`versions/版本号/`，个人数据库、配置、人格和密钥文件在`data/`。安装器不自动读取或迁移其他项目的数据，不修改系统Python/PATH，不设置开机自启。端口占用时会报错，不停止占用者。

详细步骤与隔离试用命令见[安装说明](distribution/README-INSTALL.md)。

### 源码部署

以下从仓库根目录开始。引擎代码需要Python 3.10+；安装包构建工具另需Python 3.12+。本轮实际回归使用Python 3.12/3.14，不能替代各平台和旧版本验收。

1. 从[词典发行页v0.3.1](https://github.com/luomeii/OpenGloss/releases/tag/v0.3.1)下载`dict.sqlite`，放到`resources/ECDICT/`。这是约65 MiB的公开词典，不是用户数据库。也可按[词典说明](resources/ECDICT/README.md)构建。
2. 安装API依赖并启动：

```powershell
python -m pip install -r requirements.txt
cd pae
python -m uvicorn pae_core.api:app --host 127.0.0.1 --port 4815
```

3. 等待`http://127.0.0.1:4815/v1/health`返回`ok: true`。返回值同时给出数据库路径，可核对预期实例。词典缺失或实例不可用时返回503。
4. 在浏览器扩展管理页手动加载仓库的`pae/extension`，打开英文网页。

Windows也可在`pae/`使用`start_engine.bat`、`status_engine.bat`、`stop_engine.bat`和`backup.bat`。源码启动脚本固定4815，不要用来管理安装版或其他日用实例。开机自启脚本是源码部署的可选功能，安装版不自动启用。

## 模型与角色：可选配置

**只用标注、词典释义和统计，不需要API key。** 没有模型时聊天不可用，主动表达有规则模板兜底。AI角色是补充功能，不是词级辅助的运行前提。

安装版：编辑管理页显示的`data/config.json`，在已有`llm`对象中填写供应商提供的地址、模型ID和key，保存后点击「重启此实例引擎」。

```json
"llm": {
  "base": "https://你的兼容服务地址",
  "model": "供应商提供的模型ID",
  "key": "你的API-key",
  "thinking": "disabled"
}
```

这是对象片段，需放进原配置，保留其他键。接入使用兼容`/v1/chat/completions`的接口，具体模型兼容性需实测。安装版为隔离个人环境，**不继承父进程的`PAE_LLM_*`或代理变量**，应在本实例配置中设置模型。

源码版可用当前终端环境变量`PAE_LLM_BASE`、`PAE_LLM_MODEL`、`PAE_LLM_KEY`，再从同一终端启动；也可改`pae/config.json`。源码配置被Git追踪，**不要提交真实key或个人设置**。

角色名字、口吻和设置可在人格面板调整。安装版个人修改写入`data/persona.json`，源码版默认写入`pae/persona/default.json`。

## 更新、备份与回退

安装版启动时检查公开GitHub Release，也可手动检查。只有版本高于当前、属于正式版、包含固定名`OpenGloss-windows-x64.zip`且有GitHub资产SHA256，才提供更新；预发布版本不会自动推送。

用户选择更新后，管理器下载并校验包，停止此安装引擎，以SQLite backup API备份数据，再切换程序。校验失败保留当前版；新版启动检查失败时尝试恢复旧程序与备份，并明确报告结果。断网不影响已有词级辅助。

**「回退上一版本」同时恢复更新前的数据快照**，不是只切换程序。更新后新增的记录不会出现在回退后的当前数据库中；回退前的最新数据会另存备份。更新需额外磁盘空间，保留的版本和备份目前需要用户自行管理。

扩展需手动切换到新版目录并重新加载；引擎更新不会静默安装或更新扩展。

- 安装版手工备份：先在管理页关闭此实例，确认退出，再复制整个`data/`。其中可能含密钥和对话，请私下保存。
- 源码版运行中备份：用`pae/backup.bat`（或`python pae/backup.py`）生成SQLite一致性快照；个人配置、人格和密钥需另外保存。
- 不要在运行中只复制`pae.db`。WAL模式的最新写入可能还在`pae.db-wal`。
- 分享软件用原始发行ZIP，不要把已经用过的安装目录或个人备份重新打包分享。

## 隐私与数据

数据库包含词汇事件、悬停/点击时间、阅读句子片段、对话、事实卡及参数审计。熟悉度可由词汇事件重放；人格、配置和密钥还保存在另外的JSON文件中，并非所有个人数据都在一个数据库里。

配置模型后，以下内容可能发送给你选择的模型服务：

- 聊天输入、近期对话、人格提示、事实卡和相关词汇/指标上下文。
- 启用的模型主动表达或智能体巡检所需上下文。
- 悬停词所在句子（最多300字）：用于后台义项判定影子实验，日上限150次，结果只写缓存、不改变当前展示释义。侧栏「静音」不关闭这条路径。

不配模型key时，上述模型请求无法使用。安装版管理器仍会连接公开GitHub查询发行信息，并在用户选择更新后下载包，不上传阅读记录、对话或密钥。扩展需读取网页文本才能标注，因此请求网站访问权限。

清空个人数据前先关闭对应实例，再处理它自己的目录：安装版为`data/`；源码版默认为`pae/pae.db`及关联WAL/SHM、个人配置/人格/密钥。若使用自定义路径，按实际配置定位；备份也可能保留旧数据。

## 当前验证与使用边界

已有核心事务恢复、并发访问、记忆成对写入、TTL覆盖保护、安装校验和更新回退的自动化验证，以及完整词典下的本机隔离HTTP测试。扩展自动化以模拟场景为主。

**仍需验收**：无Python的全新Windows 10/11、真实浏览器扩展联用（含夸克）、真实模型、正式GitHub安装资产的完整更新链与长期使用表现。本机短时通过不等于所有电脑都稳定，也不证明记忆效果。

已知边界：

- 引擎HTTP API默认无鉴权，只应监听本机回环，不要转发到局域网或公网。安装版管理页另有token、Origin和Host限制，不代表引擎API具备同样保护。
- 页面主世界桥消息可被恶意页面伪造或监听，当前配额只能限制部分影响，扩展隔离世界迁移仍待完成。
- `document.documentElement.dataset.paeDebug`可被页面脚本读取，包含本次注解结果。
- 统计和每日额度以UTC换日，中国时区对应早上8点。交互统计不能直接当作学习成绩。
- 冷重算仍会持锁，压力下存在延迟长尾，历史偶发提交长尾成因也尚未完全定位。没有为此降低数据库持久性设置。
- 历史事件、判重集合与数据库会随使用增长，尚未完成长期内存和历史规模验收。
- 引擎异常退出后，管理页可见未运行状态，可手动重启；当前不承诺自动崩溃重启。

## 常见问题

| 现象 | 排查 |
|---|---|
| 找不到Install.cmd | 仓库源码ZIP不含生成的安装入口；安装包名为`OpenGloss-windows-x64.zip` |
| 启动失败 | 看此实例`data/engine.log`或`data/manager-error.log`，检查端口和磁盘。源码版另确认词典 |
| 页面没有标注 | 等health就绪；确认扩展开启、加载目录和引擎地址正确；刷新普通英文网页。浏览器内置页/PDF等未作兼容承诺 |
| 不能聊天 | 检查本实例模型配置及供应商网络/额度；无key时基础功能仍可用 |
| 暂无更新或检查失败 | 没有更高正式安装资产，或无法访问GitHub；不影响已启动引擎 |
| 管理页刷新后提示重新打开 | 管理令牌由启动链接带入，用此安装快捷方式重新打开；勿分享带令牌原始链接 |

### 自定义端口

新安装可用`Install.cmd --root ".\OpenGloss-Trial" --port 54935`，管理页将使用54936。源码版用uvicorn的`--port`。先确认端口空闲，勿复用日用实例目录；同一安装重复运行不会更改已保存端口。

**扩展弹窗没有地址输入框。** 在浏览器扩展页找到OpenGloss，打开「Service Worker / 检查视图」控制台，执行以下命令，将地址替换为本实例地址，再刷新英文页面：

```javascript
chrome.storage.local.set({ paeBase: 'http://127.0.0.1:54935' })
```

安装包扩展默认4817，源码扩展默认4815，请区分所用目录。每个扩展配置只连接一个引擎。

## 开发与实现

```text
浏览器扩展 → 本机HTTP / SSE → 能力面与规则引擎 → SQLite
                              ├─ 词级标注与事件重放
                              ├─ 记忆、会话与参数审计
                              └─ 可选LLM / MCP接入
安装管理器 → GitHub Release检查 / 校验 / 版本切换与恢复
```

`state.py`、`fold.py`、`lemmatize.py`保留冻结语义及哈希校验。AI提案调参受限幅、TTL与审计约束，不自动证明调参对学习有效。核心算法、影子实验和产品效果需分别验证。

安全运行本阶段安装与恢复合同：

```powershell
python -m unittest discover -s distribution/tests -p "test_*.py" -v
node distribution/tests/test_popup.cjs
```

历史`pae/tests`部分端到端脚本会接管或停止4815，**不要在现用引擎运行时直接执行**。开发依赖见`requirements-dev.txt`；浏览器测试还需额外浏览器环境。

[构建与发行验收](distribution/BUILDING.md) · [固定公开输入与SHA256](distribution/inputs.lock.json) · [MCP接入](skills/pae-bridge/SKILL.md)

源码可通过`python pae/mcp_server.py`提供MCP，key和权限见接入说明。内部命名仍为`pae`，用于保持目录、数据库、环境变量及能力名兼容。注释中的历史规格编号是开发溯源，不要求读者拥有私有规划文档。

## 后续重点

- 全新系统、真实扩展、真实更新和长期稳定性验收。
- 用实际阅读反馈评估标注密度、熟悉度估计和重复接触效果。
- 逐步收紧页面桥边界，优化扩展地址配置体验。
- 影子实验先积累证据，再决定是否改变用户可见行为。

## 许可

代码与文档采用[MIT](LICENSE)。公开词典来自[ECDICT](https://github.com/skywind3000/ECDICT)，保留许可及署名。

`resources/ECDICT/lemma.en.txt`另有文件头声明，仅明确允许研究和教育用途，不能概括为全部MIT；商用或再分发前应核对原声明。
