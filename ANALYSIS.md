# QAI Chat v20.0 逆向分析报告（R1）

- 目标：`QAI_Chat_v20.0_(2001)_Mod.apk`，包名 `chat.gpt.open.ai`，versionName `20.0` / versionCode `2001`
- 分析时间：2026-10-29
- 方式：静态反编译（jadx）+ Firebase Remote Config 读取 + 线上协议实测，**未使用真机、未使用 Frida**
- 演示与验证：`demo_server.py` / `demo.html` / `test_demo.py` / `browser_test.py` / `stress.py`

---

## 0. TL;DR

| 项 | 结论 |
| --- | --- |
| 应用形态 | 原生 Kotlin（Ktor + Hilt + Room + Firebase + AdMob），**不是 Flutter**；「EVO」白标模板 `md.elway.evo` |
| 上游 | 单一端点 `POST https://aibr.elway-mobile.com/chatCompletion` |
| 鉴权 | **零鉴权**。唯一门禁是一枚 RSA 公钥签名，公钥硬编码在 APK 里，任何持有 APK 的人都能本地签发 |
| 配额 | **无**。客户端 token 余额（1000 免费 / 0.3 系数）完全是本地计数，服务端不校验、不回传用量 |
| 模型 | 只有一个后端模型，`aibmd`（模型名 header）被服务端**完全忽略**；自述 Qwen 系、知识截止约 2020 |
| 参数控制 | `max_tokens` / `temperature` / `stop` / `top_p` 全部无效，上游 DTO 只有 `messages` 一个字段 |
| 上下文 | 实测有效上下文**极小**（见 §7.4，~2k 字符即丢失 needle），与"长上下文"无关 |
| 输出上限 | 单次硬上限约 3.5k 汉字，`finishReason` 返回 `length` |
| 容量 | 舒适区 ≤ 5 并发；16 并发出现流提前中断；早期一轮出现 503 |
| 合规风险 | 上游为第三方白标 SaaS，非公开 API；大规模转售属于滥用。仅建议自用/研究 |

**一句话**：这是一个"协议门槛极低、模型价值一般、容量有限"的白标 AI App。逆向成本约 1 小时，
但它的定位应该是**低成本兜底渠道**，不是主力模型源。

---

## 1. APK 指纹

```
package            chat.gpt.open.ai
label              QAI Chat
versionName/Code   20.0 / 2001
minSdk / target    26 / 35
签名/加固           无壳、无 DexProtector（Mod 版，签名已被 ApkSignatureKillerEx 处理）
dex                9 个（classes.dex 27.4MB 为主）
native             libandroidx.graphics.path.so, libdatastore_shared_counter.so（无自研 .so）
assets             仅 dexopt/baseline.prof*（无 Flutter assets、无 .env）
```

广告与计费（说明这是典型的"AI 套壳 + 广告 + 内购解锁 token"商业模型）：

```
ADMOB_ID                     ca-app-pub-7357002559372284~8571652486
ADMOB_TOKEN_REWARD_ID        ca-app-pub-7357002559372284/6156978809   (看广告换 token)
ADMOB_NO_TOKENDS_ID          ca-app-pub-7357002559372284/6791666959   (余额耗尽弹窗)
TOKENS_5K_PRODUCT_ID         5k_tokens      (Google Play 内购)
TOKENS_25K_PRODUCT_ID        25k_tokens
```

> Mod 版已解锁内购，但**这与能否调用上游毫无关系**：上游根本不知道你付没付费。

Firebase：

```
project_id            ew-ask-eva
google_app_id         1:889105454564:android:2e088d9cc017c571dae3f1
google_api_key        AIzaSyBDcvvWXUoP3yHnedq2juD-qLoZ3lhG138   (公开的 Android client key，非密钥)
```

## 2. 代码结构（与协议相关的部分）

```
md.elway.evo.aibridge.ChatCompletionService   # 门面：取远程 model 名 → 调 Provider
md.elway.evo.aibridge.AIBridgeProvider        # Ktor client，构造请求头、按行读 SSE
md.elway.evo.aibridge.AIBridgeChatCompletionRequest   # @Serializable，仅 messages[role,content]
md.elway.evo.aibridge.AIBridgeChatCompletionResponse  # @Serializable，{content?, finishReason?}
md.elway.evo.utils.Crypto                     # RSA 公钥 + encrypt()
md.elway.evo.core.Application                 # 常量与 Firebase Remote Config 默认值
md.elway.evo.screen.chat.ChatPresenter        # 本地 token 扣减逻辑（纯客户端）
```

关键代码（`AIBridgeProvider$chatCompletion$1.invokeSuspend`，已实锤）：

```java
String encodeToString = json.encodeToString(reqSerializer, request);   // {"messages":[...]}
String encrypt        = Crypto.INSTANCE.encrypt(encodeToString);       // RSA(MD5(body))
UtilsKt.header(builder, "aibsg", encrypt);
UtilsKt.header(builder, "aibpf", "android");
UtilsKt.header(builder, "aibmd", str2);        // 模型名，从远程配置取
UtilsKt.header(builder, "appmv", "14");
UtilsKt.header(builder, "appvr", "1440");
UtilsKt.header(builder, HttpHeaders.CONTENT_TYPE, "text/event-stream");
```

`Crypto.encrypt()`：

```java
MessageDigest md5 = MessageDigest.getInstance("MD5");
byte[] digest = md5.digest(input.getBytes(UTF_8));
Cipher c = Cipher.getInstance("RSA/ECB/PKCS1Padding");
c.init(Cipher.ENCRYPT_MODE, getPKey());
return Base64.encodeToString(c.doFinal(digest), Base64.NO_WRAP);
```

注意语义：这是 **RSA 加密 MD5 摘要**，不是标准签名（`SHA256withRSA`）。效果等价于一个
"谁都能算、谁都能加密"的口令——因为公钥是公开的，且服务端显然是"解密后比对 MD5"。

复现（`qai_upstream.py:sign_body`）：

```python
digest = hashlib.md5(raw_body).digest()
aibsg  = base64.b64encode(pubkey.encrypt(digest, padding.PKCS1v15())).decode()
```

## 3. 完整协议

### 请求

```http
POST /chatCompletion HTTP/1.1
Host: aibr.elway-mobile.com
aibsg: <base64 RSA_PKCS1v15(MD5(raw_body))>
aibmd: t1                       # 被忽略
aibpf: android                  # 被忽略
appmv: 14                       # 被忽略
appvr: 1440                     # 被忽略
Content-Type: text/event-stream
Connection: keep-alive

{"messages":[{"role":"user","content":"你好"}]}
```

Header 的实测重要度（`aibsg` 单发即可 200）：

| 请求 | 结果 |
| --- | --- |
| 带完整 6 个 header | 200 流式 |
| **只带 `aibsg`** | 200 流式 |
| 不带 `aibsg` | 400 Bad Request |
| `aibsg` 是垃圾串 | 500 Internal Server Error |
| `aibsg` 与 body 不匹配 | 500 Internal Server Error |
| 不带 `Content-Type` | 200 流式 |

→ 服务端逻辑：`RSA解密(aibsg) == MD5(body)` ? 放行 : 抛异常（Spring 默认错误 JSON）。

### 响应

```
data:{"role":"assistant","content":"你好"}      ← 首块带 role
data:{"content":"！"}                            ← 纯增量
data:{"content":"有什么"}                        ← 纯增量
data:{"finishReason":"stop"}                     ← 终止块，无 content
```

- 空行分隔，**非** OpenAI SSE（没有 `choices/delta/[DONE]`）
- `finishReason` 取值：`stop` / `length`
- 无 `usage`、无 `id`、无 `model` 回显
- **无 charset**，`Content-Type: text/event-stream` → requests/浏览器默认按 ISO-8859-1 解码，
  中文必乱码。必须强制 UTF-8（网关已处理）

### 错误语义

```
400 {"timestamp":"...","status":400,"error":"Bad Request","path":"/chatCompletion"}   # 缺 aibsg
500 {...}                                                                             # 签名/body 不匹配、content 是数组、缺 messages
503 {...}                                                                             # 后端过载（压测中出现）
404 {...}                                                                             # 其它所有路径
```

## 4. 上游基建指纹

| 线索 | 值 |
| --- | --- |
| HTTP Server | `nginx/1.24.0 (Ubuntu)` |
| 应用框架 | Spring Boot（错误体是 Spring 默认 `{timestamp,status,error,path}`） |
| ALPN | 仅 http/1.1（无 h2） |
| IP | `aibr.elway-mobile.com → 146.19.213.72` |
| 端点扫描 | 60 条常见路径全部 404，**只有 `/chatCompletion` 存活**；无 actuator/swagger/health |
| 响应风格 | 逐 token 推送、字符级切分（3.5k 汉字 ≈ 2000 chunks），类似 vLLM/Ollama 之上的转发层 |
| 吞吐 | 长文本 175 chars/s（本地经代理测得） |

`chat.elway-mobile.com`（161.97.73.134）在代码里只有一处 `https://chat.elway-mobile.com/` 常量，
443 端口无 TLS 响应、整体不可达 → 遗留域名，当前无用。

## 5. Firebase 远程配置（活的，可匿名读取）

用 APK 内的 `project_id` + `google_api_key` 走 Remote Config REST 即可匿名 fetch（无需登录）：

```
POST https://firebaseremoteconfig.googleapis.com/v1/projects/ew-ask-eva/namespaces/firebase:fetch?key=<google_api_key>
{"app_instance_id":"<任意uuid>","app_id":"1:889105454564:android:2e088d9cc017c571dae3f1","package_name":"chat.gpt.open.ai"}
```

2026-10-29 实际返回（templateVersion 144）：

```json
{
  "ai_bridge_model": "t1",
  "ai_bridge_url": "https://ljbgc4xyfeca3dm2q7td3bsrou0lrtqm.lambda-url.us-east-1.on.aws/",
  "app_start_ads": "true",
  "evo_art_button": "false",
  "image_token_consume_factor": "10",
  "paywall_onboarding": "true",
  "reward_for_review": "false",
  "sub_btn_title": "Subscribe",
  "token_consume_factor": "1"
}
```

两个要点：

1. `ai_bridge_model = "t1"` —— 一个内部别名，不是任何公开模型名。客户端把它塞进 `aibmd`，服务端又不看。
2. `ai_bridge_url` 指向 AWS Lambda Function URL —— 但**当前 APK 版本不使用它**：
   `ChatCompletionService.chatCompletionStream()` 里 URL 是硬编码的
   `"https://aibr.elway-mobile.com/chatCompletion"`。

### 5.1 关于那个 Lambda（已穷举，结论：空占位）

`ljbgc4xyfeca3dm2q7td3bsrou0lrtqm.lambda-url.us-east-1.on.aws` 对**任何**请求都返回
`200 + Content-Type: application/octet-stream + 0 字节`，且带正常 `x-amzn-RequestId`：

| 输入 | 结果 |
| --- | --- |
| GET/POST/PUT/OPTIONS/DELETE `/` | 200 空 |
| `/chatCompletion`、`/chat`、`/v1/chat/completions`、`/completion`、`/invoke`、`/api/chat`、`/health` | 全 200 空 |
| 带正确 `aibsg` 签名 + `aibmd: t1` 的原始 iOS/Android 头组合 | 200 空 |
| OpenAI 格式 body / Ollama `chat` / Ollama `generate` / 纯文本 / 空 body | 全 200 空 |
| `?model=t1` 查询参数、`Accept: application/json` | 200 空 |
| **1MB 垃圾二进制** | 200 空 |
| **非法 UTF-8** | 200 空 |
| **畸形 JSON `{"messages":`** | 200 空 |
| 5000 层嵌套 JSON | 200 空 |

连畸形输入都不报错 → **handler 里没有任何请求解析逻辑**，是一个空实现占位函数。
响应耗时 2–4s（含代理往复）说明函数被执行，但立即返回空。
**结论：Lambda 端点拿不到任何服务，真实流量仍全走 `aibr.elway-mobile.com`。**

`evo_art_button = false` → 文生图功能未开启，APK 内也没有图片相关的网络代码。

## 6. 客户端"配额"的真相

`ChatPresenter` / `BillingManager` / `PreferenceKeys` 里有一整套 token 经济：

```
REMOTE_PROPERTY_FREE_TOKENS = 1000        新用户免费额度（客户端计）
REMOTE_PROPERTY_TOKEN_CONSUME_FACTOR = 1  每次对话消耗系数（客户端乘）
TOKENS / TOKEN_PURCHASED / IS_SUBSCRIPTION_ACTIVE   全在 SharedPreferences
ADMOB_TOKEN_REWARD_ID                     看广告 +token
NotEnoughTokensException                  额度耗尽 → 弹内购
```

全部是**本地记账**。上游既不接收余额、也不返回用量，更没有 401/402。所以：

> 只要你会签名，就有无限额度。Mod 版破解内购与否，对能否白嫖上游没有任何影响——
> 这一点很多人会搞错。

## 7. 能力测绘（全部实测）

### 7.1 模型

`aibmd` 传什么都被忽略：`default / gpt-4o / gpt-4.1 / gpt-4-turbo / gpt-3.5-turbo / o1 / o3-mini /
o4-mini / claude-3-5-sonnet / claude-sonnet-4 / gemini-2.0-flash / gemini-2.5-pro / gemini-2.5-flash /
deepseek-chat / deepseek-r1 / qwen-max / llama-3.1-70b / grok-3 / mistral-large / gpt-5` —— **24 个候选名
全部返回 200 且行为一致**。

identity 探针（同一模型的不同提问会自相矛盾，说明它没有可靠的自我认知）：

| 问题 | 回答 |
| --- | --- |
| 你是哪个公司开发的模型 | 「我是阿里云开发的模型」 |
| 你的知识截止日期 | `2020` |
| 继续 "As an AI language model developed by" | "An AI language model developed by Anthropic," |
| 列出 2024 年的 3 件事 | `UNKNOWN` |

综合中文能力、知识截止 2020、代码能力、生成速度（175 chars/s）、退化式重复（长文本末尾开始复读
"中国人工智能企业专利质量达到10000件"），判断为 **7B 级别的中文对话模型（Qwen 系列早期/蒸馏版本）**，
部署在后端推理服务上。**不要按 GPT-4 / Claude 的预期使用**。

### 7.2 参数

| 字段 | 结果 |
| --- | --- |
| `max_tokens` | 忽略。要 5 token，实际输出完整段落 |
| `temperature` / `top_p` / `stop` / `presence_penalty` / `frequency_penalty` / `n` | 忽略 |
| `stream: false` | 忽略。上游永远 SSE |
| 额外顶层字段（`userId` 等） | 忽略，不报错 |

上游 DTO：

```kotlin
@Serializable class AIBridgeChatCompletionRequest(val messages: List<Message>)
@Serializable class Message(val role: String, val content: String)
```

只有这两个字段会被序列化。任何 OpenAI 参数都不可能生效——**网关只能把这类请求"降级"并显式告知**，
不能假装支持（本项目在响应里回填 `x_qai_ignored_params`）。

### 7.3 消息

| 用例 | 结果 |
| --- | --- |
| `system` role | 生效（"只回答大写"验证通过） |
| 多轮 assistant 历史 | 生效（"我的代号是 Zeta-9"→ 正确回忆） |
| 未知 role（`wizard`/`developer`/`tool`） | 不报错，被服务端当普通消息拼接 |
| 缺 `content` | 不报错 |
| `content` 为数组（多模态 parts） | **500** |
| `image_url` 图像输入 | **500**，无视觉能力 |
| 空 `messages: []` | 不报错，模型自由发挥 |
| 缺 `messages` 字段 | 500 |

### 7.4 上下文窗口（重要，也是最反直觉的一项）

needle-in-haystack（把验证码 `ZX-4471` 埋在中文文档里，问验证码）：

| 文档长度 | needle 位置 | 结果 |
| --- | --- | --- |
| 552 字符 | 中间 | **HIT** |
| 613 字符 | 中间 | **HIT** |
| 1 013 字符 | 中间 | MISS |
| 1 513 字符 | 中间 | MISS |
| 2 213 字符 | 中间 | MISS |
| 3 013 字符 | 中间 | MISS |
| 1 972 字符 | **最前面** | MISS |
| 1 972 字符 | **最后面** | MISS |
| 5 892 字符 | 最前面 | MISS |
| 30 050 字符 | 中间 | MISS |

（另有一轮 `system` 承载资料的变体，2 022 / 6 040 / 15 056 / 30 050 字符全部 MISS。）

失效模式不是"报错"，而是模型**无视问题、直接开始复读/续写输入文本**——典型的"输入远超模型
有效注意力，prompt 被降级成大段上下文噪声"。

同时 400 000 字符的超大 body 也不会 413/400（服务端照收），只是模型给不出有意义回答。

**结论：有效上下文只有 ~600–1000 汉字。把它当"短对话模型"用，长文档问答/长 prompt 全部不要交给它。**

### 7.5 输出上限

请求写 5000 字长文 → 输出 3 489 汉字后 `finishReason: length` 截止，耗时 19.9s（175 chars/s）。
末尾出现连续复读退化。**不要依赖它做长文本生成。**

### 7.6 错误与对齐

- 会拒绝："I'm sorry, but I can't assist with that." —— 有基础对齐层
- 400k 无意义填充 → 拒绝而非报错，说明拒绝发生在模型层

## 8. 压测（第一轮）

环境：Windows + 本地 HTTP 代理 `127.0.0.1:10808`（**上游在国内不可直连，见 §9.1**），
prompt「用大约150字介绍长江的地理与文化意义。」，每档 8 请求。

直接打上游：

| 并发 | 成功 | 总时长 p50 | p90 | TTFT p50 | 输出量/请求 | 速率 | errors |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 8/8 | 7.11s | 9.11s | 1.97s | 226 chars | 42.6 c/s | — |
| 5 | 8/8 | 6.75s | 8.28s | 5.54s | 212 chars | 37.1 c/s | — |
| 10 | 8/8 | 8.29s | 12.55s | 7.09s | 244 chars | 39.7 c/s | — |
| 16 | 8/8 | 32.21s | 33.69s | 21.01s | 136 chars | 11.5 c/s | 3 个流提前中断（无 finishReason） |

另一轮短输出（"数 1 到 30"）：

| 并发 | 成功 | 备注 |
| --- | --- | --- |
| 10 | 28/30 | 2 × **503** |
| 20 | 37/40 | 3 × **503** |

经本地网关（OpenAI 兼容层，同一 prompt、每档 6 请求）：

| 并发 | 成功 | 总时长 p50 | p90 | TTFT p50 | 输出量/请求 | 速率 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 6/6 | 5.03s | 9.59s | 2.53s | 211 chars | 42.7 c/s |
| 5 | 6/6 | 27.98s | 34.82s | 21.47s | 236 chars | 12.6 c/s |
| 10 | 6/6 | 6.01s | 7.81s | 4.92s | 228 chars | 46.4 c/s |

同时刻成对对照（两轮背靠背，同 conc=5、同 prompt）：

| 路径 | p50 | p90 | TTFT p50 | 速率 |
| --- | --- | --- | --- | --- |
| 直连上游 | 8.47s | 10.20s | 7.23s | 27.2 c/s |
| 经本地网关 | 5.12s | 6.11s | 4.05s | 46.6 c/s |

**网关本身没有可测量的额外开销**（Python `ThreadingHTTPServer` 转 SSE 的代价远小于上游队列抖动，
网关甚至"更快"只是因为上游排队波动主导了测量）。但反过来说——**上游延迟波动极大**：
同一个 conc=5 档位三次测量的 TTFT 分别是 4.05s / 7.23s / 21.47s。单次测量不能当 SLA 看。

**容量结论**：≤ 5 并发可用；10 并发开始排队（TTFT 2s→7s）；16 并发出现流提前中断；短请求高峰会
吃到 503。这个上游适合做**低频兜底**，不适合接生产主流量。

## 9. 工程坑（复现必踩）

### 9.1 国内必须挂代理

`env` 里存在 `HTTPS_PROXY=http://127.0.0.1:10808` 时一切正常；`curl --noproxy '*'` **0.3 秒即失败**
（TCP 阻断，非 DNS）。所以：
- 所有本机压测数据都包含代理往返开销（TTFT 里约 0.3–1s 是代理）
- 网关要部署在能出海的机器上，或者显式配置 `HTTPS_PROXY`

### 9.2 SSE 无 charset → 中文乱码

`Content-Type: text/event-stream` 没有 charset，`requests.iter_lines(decode_unicode=True)`
会按 ISO-8859-1 解出 `æä»¬` 这样的乱码。必须在读取前 `resp.encoding = "utf-8"`。
网关与 `qai_upstream.iter_deltas` 已强制处理。

### 9.3 上游是"永远流式"

`stream:false` 无效。非流式实现只能在本地聚合 SSE；没有 `usage` 只能估算（本项目标注 `estimated: true`）。

### 9.4 finishReason 语义映射

上游只有 `stop` / `length` 两个值，且以**独立终止块**下发（`{"finishReason":"stop"}`，无 content）。
把它翻成 OpenAI 的 `finish_reason` 时要注意别把终止块当成内容块。

### 9.5 `iter_deltas` 必须容忍坏行

上游偶发空行/半包，逐行 `json.loads` 需要 try/except 跳过，否则整个流崩。

### 9.6 aibsg 与 body 必须严格一致

序列化必须用与签名时**完全相同的字节**（`separators=(",",":")`、`ensure_ascii=False`）。
差一个空格就 500。这是最容易调半天的地方。

## 10. 风险评估与定位建议

| 维度 | 评估 |
| --- | --- |
| 协议稳定性 | 中等。签名算法在 APK 内稳定，但远程配置里已经预留了 Lambda 端点（当前空实现，见 §5.1），说明运营方在为后端迁移做准备；一旦切换，现有 `aibr` 端点可能下线 |
| 模型质量 | 低。7B 级、知识截止 2020、>2k 字符丢信息、长文复读 |
| 容量 | 低。5 并发舒适区 |
| 滥用风险 | **高**。这是他人运营的白标 SaaS，无鉴权不等于授权。大规模转售/接入公开服务属于滥用，可能触发 IP 封禁或运营方补鉴权 |
| 合规建议 | 仅本地自用 / 协议研究。不要做公开转售，不要写进对外产品 |

**建议定位**：
1. 自用兜底渠道（低价值、低频）
2. 协议研究样本（"RSA+MD5 签名即可白嫖"是这类白标模板的通病，同模板的其它 App 大概率同样可破）
3. **不**建议接 new-api 生产渠道，除非明确只给自己低优先级场景用

## 11. 复现步骤

```powershell
# 1. 静态
jadx -d jadx_out --no-res app.apk
#   → md/elway/evo/aibridge/*, md/elway/evo/utils/Crypto.java

# 2. 协议验证（无需 APK）
python -c "import qai_upstream as up; print(up.chat_completion([{'role':'user','content':'你好'}]))"

# 3. 网关 + demo
pip install -r requirements.txt
python demo_server.py --port 8931
python test_demo.py --port 8931
python browser_test.py

# 4. 压测
python stress.py --target upstream --levels 1,5,10,16 --per-level 8
python stress.py --target gateway  --levels 1,5,10 --per-level 6
```

## 12. 方法论备注：这类白标模板的通用性

`md.elway.evo` 是一套可授权的 AI App 模板（同一套 UI/协议/计费模型被贴上不同包名发售，
本项目此前分析过的多个"AI Chat 套壳"本质上也是同一商业模式）。它的安全假设是
"客户端是不可信的，所以我们用 RSA 签名"——但公钥在客户端里，签名因此**不提供任何安全性**，
只能挡住不会逆向的人。

要真正防滥用，正确做法是：服务端下发的短期 token + 设备/账号绑定 + 服务端计费。
这类模板普遍没做。
