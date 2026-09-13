# 5.2 Web 服务化 —— FastAPI 与前端模块化

> 定位：把 CLI 里的问答链路变成一个多用户 Web 系统，并让前端**能被持续扩展**（而不是每加一个功能就改一遍主页面）。

本节目标：

1. 掌握 Web 入口的认证设计（bcrypt + JWT 放 HttpOnly Cookie）；
2. 理解 SSE 事件流的设计与"兜底/出错路径也要对付"的工程细节；
3. 掌握**模块注册表驱动导航**这一可扩展前端结构；
4. 说清"起诉状生成模块先只做前端骨架"的取舍。

---

## 1. 为什么要有 Web 入口

CLI 适合开发调试，但产品形态需要三样 CLI 给不了的东西：

| 需求 | CLI | Web |
|---|---|---|
| 多用户与身份隔离 | 一个终端一个人 | 注册/登录 + `user_id` 贯穿全链路 |
| 历史回看与引用溯源 | 滚回去看，关掉就没了 | 多会话列表 + 引用面板可展开 |
| 流式体感 | `print` 逐字 | 浏览器打字机效果 |

关键点：**两条入口共用同一条问答链路**（`app/kb.py`），区别只在"用户身份从哪来"——CLI 的 `user_id` 就是会话 ID，Web 的来自 JWT。

---

## 2. 认证：bcrypt + HttpOnly Cookie

```
POST /api/auth/register   → 注册即登录（直接下发 Cookie）
POST /api/auth/login      → 登录
POST /api/auth/logout     → 登出（清 Cookie）
GET  /api/auth/me         → 当前用户（前端进站先问它判断登录态）
```

### 2.1 密码：bcrypt

```python
# 显式截断 72 字节（bcrypt 的有效输入上限）
```

为什么强调"显式截断"？因为 bcrypt 只取前 72 字节，**超出部分被静默忽略**——不显式处理的话，"密码前 72 字节相同"的两个密码会互相通过校验。显式截断让这个行为变成**代码里明写的约定**，而不是依赖库的隐含行为。

### 2.2 令牌：JWT 放 HttpOnly Cookie

```python
# 前端读不到令牌，也不该读到
# JWT_SECRET（生产必须用环境变量覆盖）/ JWT_EXPIRE_DAYS（默认 7）
```

| 设计 | 防的是什么 |
|---|---|
| **HttpOnly** | JS 读不到 Cookie → **XSS 偷不走令牌**（这是 localStorage 方案最大的软肋） |
| **SameSite=Lax** | 跨站请求不自动带上 Cookie → 减轻 CSRF |
| **与 API 同源托管** | 免去 CORS 配置（也免去"跨域 Cookie 要不要 `SameSite=None`"的两难） |
| **登录失败不区分账号是否存在** | 防**账号枚举**——否则攻击者能靠报错差异探测哪些用户名已注册 |

> 安全上的一个通用判断：**认证信息的存放位置决定了攻击面**。放 localStorage → 怕 XSS；放 Cookie → 怕 CSRF。这里的组合（HttpOnly + SameSite=Lax + 同源）是把两边都压到一个可以接受的水平，而"同源托管"这个决定顺手把 CORS 的复杂度也消掉了。

---

## 3. 会话 CRUD 与越权防护

```
GET    /api/sessions                    → 会话列表
POST   /api/sessions                    → 新建会话
GET    /api/sessions/{id}/messages      → 历史消息（含 citations）
DELETE /api/sessions/{id}               → 删除会话（连带清长期记忆）
POST   /api/chat                        → SSE 流式问答
```

### 3.1 越权一律 404

```python
# 所有会话接口先校验归属
if not session_belongs_to(session, user_id):
    raise HTTPException(404)
```

三个细节：

| 细节 | 理由 |
|---|---|
| 不属于当前用户一律 **404**（不是 403） | **不暴露"这个会话存在"**——403 等于告诉攻击者"你猜对了 ID，只是没权限" |
| 校验**在检索之前** | 越权请求**不触发检索与生成**——不浪费算力，也不产生"别人的问题被检索了"的日志污染 |
| 删除会话**连带清长期记忆** | 否则"已删内容仍会被召回"（5.1） |

---

## 4. SSE 事件流

### 4.1 为什么不用 `EventSource`

浏览器原生的 `EventSource` **只支持 GET**，而问答是 POST（要带问题正文）。所以前端用 `fetch + ReadableStream` **手动解析 SSE**：

```javascript
// web/src/api.js
const res = await fetch('/api/chat', { method: 'POST', credentials: 'include', ... })
const reader = res.body.getReader()
// 手动按 "event: xxx\ndata: {...}\n\n" 分块解析
```

服务端就是几行字符串拼接：

```python
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
```

### 4.2 事件顺序

**正常路径**：

```
meta        {citations, retrieval_ms, ...}     ← 先给引用与耗时（用户可先看依据）
delta *     {text}                             ← 正文分片（打字机效果）
citations   {citations}                        ← 引用最终确认
memory      {added}                            ← 本轮记忆写入情况
done        {}
```

**分支路径**：

| 路径 | 事件 |
|---|---|
| 意图非"可答" | `fallback` {intent, answer} → `done`（**不进检索与生成**） |
| 生成失败 | `error` {message} → **仍然** `citations` → `memory` → `done` |

### 4.3 两个容易忽略的设计

**① `meta` 先于 `delta`。** 先把引用和耗时推给前端，用户可以在正文还在生成时就看到"依据是哪几条"——这对法律场景很重要（用户最关心的是"凭什么"）。

**② 生成失败仍然返回引用并写记忆。** 这是 4.5 讲过的原则："部分成功时，把已经确定的信息全部保留下来"。排查"某次为什么没答出来"时，`citations` 是唯一能说明"当时检索到了什么"的证据；记忆不写则会造成多轮上下文断层。

---

## 5. 依赖注入：让接口测试完全离线

```python
# deps.py：当前用户 / KB / 记忆 / 生成器 / 分类器 全部走 Depends
# 测试用 app.dependency_overrides 换成假实现 → 接口测试不连 MySQL/Milvus/LLM
```

这个设计让"接口层测试"变成纯内存操作（几毫秒一个），是第 5 章"217 项测试全离线"的基础。

⚠️ **一个必须遵守的约束**：

```python
# ❌ 依赖内部直接调用别的依赖函数
def current_user(request):
    store = get_memory_store()        # 直接调用 → 测试替换失效！

# ✅ 必须写成 Depends 参数
def current_user(request, store=Depends(get_memory_store)):
    ...
```

原因：`dependency_overrides` 只替换"作为依赖被解析"的函数。依赖内部**直接调用**别的依赖函数时，那次调用**绕过了替换机制**——测试以为注入了假实现，实际跑的还是真的。这个坑很隐蔽（测试"看起来过了"，其实连了真库），所以有一条测试专门卡住它。

---

## 6. 前端模块化：注册表驱动的导航

### 6.1 问题

如果要加"起诉状生成""合同审查"等模块，最自然的写法是在主页面里加 `if/else` 切页面、再加几个导航按钮。结果是**每加一个模块就改一遍主页面**，主页面逐渐变成"什么都知道"的上帝组件。

### 6.2 做法：一张注册表

```javascript
// web/src/modules.js —— 系统扩展的唯一入口
export const MODULES = [
  { key: 'chat',      glyph: '◈', label: '法律问答',
    hint: '混合检索法条 + 综合生成回答',      component: ChatPage,      enabled: true  },
  { key: 'complaint', glyph: '▤', label: '起诉状生成',
    hint: '按要素生成民事起诉状（开发中）',    component: ComplaintPage, enabled: false },
]
```

```
AppShell.jsx
  ├─ 顶栏：品牌 + 当前模块名 + 用户/登出
  ├─ 导航条：MODULES.map(...)  ← 横向 tab，当前项金色下划线
  └─ 内容区：<Active.component />  ← 模块组件
```

**加一个模块 = 在数组里追加一条记录**，`App.jsx` / `ChatPage` 都不用改。

### 6.3 `enabled: false`：入口先曝光

未就绪的模块**照常出现在导航里**，带一枚「开发中」标记：

| 好处 | 说明 |
|---|---|
| 用户提前知道系统要往哪走 | 而不是"某天突然多一个功能" |
| 前端可以先迭代界面与字段 | 后端能力就绪后把 `enabled` 改成 `true` 即可 |
| 边界诚实 | 点进去看到的是"表单 + 本地预览 + 置灰按钮"，不会误导 |

> 这个做法也是 5.2 的一个小方法论：**把"未完成"显式建模成数据（`enabled: false`），而不是藏起来或假装完成。**

---

## 7. 正文 Markdown 统一入口

```jsx
// web/src/components/MarkdownBody.jsx
<Markdown remarkPlugins={[remarkGfm]} components={{ table: ... }} />
```

**踩过的坑**：`react-markdown` 默认只解析 CommonMark，**不含 GFM**——不挂 `remark-gfm` 插件时，**回答里的 markdown 表格会原样当普通文字吐出来**（`| a | b |` 和 `| --- | --- |` 直接显示出来）。

配套的两个细节：

| 细节 | 理由 |
|---|---|
| 表格外包一层 `overflow-x: auto` | 消息气泡本身不定宽，宽表会**撑破布局** |
| 渲染 markdown 一律走这一个组件 | 新模块不必各自配置插件——**漏挂插件就是上面那个坑** |

---

## 8. 起诉状生成：先出前端骨架的取舍

这个模块目前是 `enabled: false` 的状态，做法值得单独讲：

| 做了什么 | 为什么 |
|---|---|
| 完整表单（原告/被告/诉讼请求/事实与理由/证据清单/受诉法院） | 把**字段**定下来——字段是接口设计的一部分 |
| **本地拼接的实时预览**（不经过服务端） | 把**文书格式**定下来；预览用同一个 `MarkdownBody` 渲染，顺带验证了表格/列表渲染 |
| 提交与导出按钮**置灰** + 「开发中」标注 | 诚实表达边界（见 6.3） |
| **不新增任何后端接口** | 避免"接口先定了、实现后发现字段不对"的返工 |

后续接入方式很简单：**把 `buildComplaint()` 换成调后端返回的文书即可，表单与预览不动**。

> 边界说明：起诉状属于**文书生成**，与"问答"是两类任务。若要给它加"引用法条"的能力，应当**复用 `KnowledgeBase.search`**（第 3 章那条链路）而不是重造一个检索——这也是模块化结构的价值：**能力复用而不是复制**。

---

## 9. 本节小结

- 认证：**bcrypt**（显式截断 72 字节）+ **JWT 放 HttpOnly + SameSite=Lax Cookie**（XSS 偷不走、减轻 CSRF）+ **同源托管**（免 CORS）；登录失败**不区分**账号是否存在（防枚举）。
- 会话接口的越权防护：不属于当前用户一律 **404**（不暴露存在性），且**校验在检索之前**；删会话连带清长期记忆。
- SSE：`EventSource` 只支持 GET，所以 `fetch + ReadableStream` 手动解析；事件顺序 `meta → delta* → citations → memory → done`，兜底走 `fallback`，生成失败发 `error` 但**仍返回 citations 并写记忆**。
- 依赖注入让接口测试完全离线；**依赖内部不要直接调用别的依赖函数**（会让 `dependency_overrides` 失效）。
- 前端用**模块注册表**（`modules.js`）驱动导航——**加模块只改一张表**；未就绪模块用 `enabled: false` **显式建模为数据**，照常曝光并标「开发中」。
- Markdown 渲染统一走 `MarkdownBody`（内置 `remark-gfm`）——**不挂 GFM 插件表格会原样吐出**；表格要外包 `overflow-x: auto`。

## 企业常见面试问答

**Q1：为什么用 Cookie 存令牌，不用 localStorage？**

> 因为**攻击面不同**。localStorage 的问题是完全暴露给 JS——一旦站点有 XSS，攻击者一行 `localStorage.getItem('token')` 就把令牌拿走了，而且这个令牌可以拿到任何地方用。Cookie 加 `HttpOnly` 之后 JS **根本读不到**，XSS 就偷不走令牌。代价是引入了 CSRF 的风险（浏览器会自动带上 Cookie），我们用 `SameSite=Lax` 缓解，再加上**前端与 API 同源托管**——同源意味着不存在跨域请求，CSRF 的经典路径基本被堵死，同时也免去了 CORS 配置和"跨域 Cookie 要不要 `SameSite=None`"这个两难。所以这不是单点选择，是**三个决定配合起来**的方案。

**Q2：SSE 和 WebSocket 怎么选？**

> 看通信方向。**SSE 是单向的**（服务端 → 浏览器），正好匹配"用户问一次、服务端流式吐回答"这个场景；WebSocket 是全双工，能力更强但复杂度也更高（连接管理、心跳、重连、协议设计）。我们的需求里不需要"客户端随时主动推消息给服务端"，所以 SSE 更合适——它就是 HTTP 上的一个长响应，实现简单、天然兼容现有认证（Cookie 照样带）、断了浏览器还能自动重连。**唯一的不便是 `EventSource` 只支持 GET**，而问答需要 POST 传问题正文，所以前端用 `fetch + ReadableStream` 手动解析 SSE 格式——多写几十行解析代码，但换来协议上的简单。

**Q3：前端怎么保证可扩展？**

> 关键是**把"有哪些模块"从代码结构里抽出来变成数据**。我们用一张注册表（`MODULES` 数组），每项声明 `key/label/component/enabled` 等；`AppShell` 遍历它渲染导航条与内容区。于是"加一个模块"= **在数组里追加一条**，不需要动 `App.jsx` 或任何已有页面——避免主页面变成"什么都知道"的上帝组件。另外两个配套决定：① 未就绪的模块用 `enabled: false` **显式建模**，照常出现在导航里并标「开发中」，这样"未完成"是一个可查询的状态，而不是靠口头约定；② 公共能力（比如 markdown 渲染）抽成组件，新模块**复用而不是复制**——否则每加一个模块就多一处漏挂 `remark-gfm` 的机会。

**Q4：为什么接口测试能完全离线？**

> 靠 FastAPI 的**依赖注入**：当前用户、KB、记忆、生成器、分类器全部通过 `Depends` 获取，测试里用 `app.dependency_overrides` 把它们替换成假实现。这样接口测试不需要 MySQL、Milvus、Neo4j，也不需要 LLM——跑一个用例几毫秒。这里有一个**必须遵守的约束**：依赖函数内部**不能直接调用别的依赖函数**（比如 `current_user` 里直接 `get_memory_store()`），否则那次调用绕过了替换机制，测试以为注入了假实现、实际连的是真库——**测试"看起来过了"但什么都没测到**。这个坑我们踩过，现在有一条测试专门卡住它。原则是：**依赖关系要显式声明在参数上，不要藏在函数体里。**
