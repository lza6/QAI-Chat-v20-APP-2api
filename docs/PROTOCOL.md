# PROTOCOL.md — QAI Chat v20.0 上游协议规格

可独立于本仓库使用：任何人拿到这份文档 + 一枚公钥 + 一个能出海的网络出口，就能复现全部能力。
协议逆向自 `chat.gpt.open.ai`（QAI Chat 20.0 / versionCode 2001），2026-10-29 实测通过。

---

## 1. 端点

```
POST https://aibr.elway-mobile.com/chatCompletion
```

- 唯一存活端点。同一域名下扫过 60 条常见路径（`/v1/chat/completions`、`/chat`、`/health`、
  `/actuator/*`、`/swagger*` …）全部 404。
- 服务端指纹：`nginx/1.24.0 (Ubuntu)` + Spring Boot（错误体是 Spring 默认结构），仅 http/1.1。
- `https://aibr.elway-mobile.com/` 根路径返回 nginx 默认欢迎页。

## 2. 鉴权：一枚 RSA 公钥

请求头 `aibsg` 的值：

```
aibsg = Base64Std( RSA_PKCS1v15_encrypt( MD5(raw_request_body_bytes) ) )
```

- RSA 密钥 2048 位，公钥以 X.509 SubjectPublicKeyInfo 形式**硬编码在 APK 里**
  （`md.elway.evo.utils.Crypto.getPKey()`）。
- 语义不是标准签名，而是"用公钥加密摘要"；服务端解密后与 `MD5(body)` 比对。
- **公钥是公开的**，所以这道门禁挡不住任何人——它只挡不会逆向的人。
- 该校验的实测边界：

| 请求 | 结果 |
| --- | --- |
| 只带 `aibsg` | **200**（`aibpf`/`aibmd`/`appmv`/`appvr` 都不需要） |
| 不带 `aibsg` | `400 Bad Request` |
| `aibsg` 是垃圾串 | `500 Internal Server Error` |
| `aibsg` 与 body 不匹配 | `500 Internal Server Error` |
| 不带 `Content-Type` | 200 |

公钥（可直接复制）：

```
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA7TIHog89K/IEIO3LF2/B9A8Gi/DkXZG6Y+PoydoKD9JrxR9CHRP1Pmbsm09oY9ycxyw7Tfb4ljt9ySxOmzMALoN1mJx0ontUe9+BoAxddK2NQDhRz55B/HUUAGFJ9tGTC8dlkAgc0OnEBR9O60x5KDOltgNR/iriGdlxMoTGRnGS6LOXGiRQlAzyj1hmyTl9vOePkWTvj/GG3W1sWaw9LTvd83WZdxvgGtcjMe4ZqW86BGTmG32yqgg+uQCew88BaSRAmTxpH/YqKWsBD7mk5pS80+ss2mS6XeQRtqN82DActjrgOoOiaL9nZdh7DZJIY2Sqz8aHh9vkHeqwJi1iDwIDAQAB
```

### 2.1 跨语言实现

**Python**

```python
import base64, hashlib
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

PUB = "<上面的 base64>"
key = serialization.load_der_public_key(base64.b64decode(PUB))

def sign(raw_body: bytes) -> str:
    digest = hashlib.md5(raw_body).digest()          # 16 字节
    return base64.b64encode(key.encrypt(digest, padding.PKCS1v15())).decode()
```

**Go**

```go
der, _ := base64.StdEncoding.DecodeString(publicKeyB64)
parsed, _ := x509.ParsePKIXPublicKey(der)
pub := parsed.(*rsa.PublicKey)
digest := md5.Sum(rawBody)
sig, _ := rsa.EncryptPKCS1v15(rand.Reader, pub, digest[:])
aibsg := base64.StdEncoding.EncodeToString(sig)
```

**Node**

```js
const crypto = require('crypto');
const key = crypto.createPublicKey({ key: Buffer.from(PUB, 'base64'), format: 'der', type: 'spki' });
const digest = crypto.createHash('md5').update(rawBody).digest();
const aibsg = crypto.publicEncrypt({ key, padding: crypto.constants.RSA_PKCS1_PADDING }, digest).toString('base64');
```

> 🔴 **关键约束**：签名必须针对**即将发送的那一串原始字节**。序列化时如果
> `separators`/`ensure_ascii`/空格有任何不一致，服务端就返回 500。这是最容易调半天的地方。

## 3. 请求头

```http
Content-Type: text/event-stream
aibsg: <签名>
aibmd: t1          # 模型别名，服务端完全忽略
aibpf: android     # 忽略
appmv: 14          # 忽略
appvr: 1440        # 忽略
Connection: keep-alive
```

`aibmd` 的值来自 App 的 Firebase 远程配置 `ai_bridge_model`（当前为 `t1`）。

## 4. 请求体

```json
{"messages":[{"role":"user","content":"你好"}]}
```

- 上游 DTO 只有 `messages[].role` 和 `messages[].content` 两个字段。
- `content` **必须是字符串**。传 `[{ "type": "text", ... }]` 数组会 `500`（无多模态）。
- 额外字段（`max_tokens`/`temperature`/`stream`/`top_p`/`stop`/`userId`…）全部被静默忽略。
- 未知 role（`developer`/`tool`/任意字符串）不报错，被当普通消息拼接。
- 缺 `content` 不报错；缺 `messages` 返回 500；`messages: []` 不报错（模型自由发挥）。

## 5. 响应

`text/event-stream`，每个 `data:` 行一个 JSON 对象：

```
data:{"role":"assistant","content":"你"}     ← 首块带 role
data:{"content":"好"}                        ← 纯增量
data:{"content":"！"}
data:{"finishReason":"stop"}                 ← 终止块，无 content
```

字段：

| 字段 | 出现时机 | 取值 |
| --- | --- | --- |
| `role` | 仅首块 | `assistant` |
| `content` | 每个增量块 | 字符串 |
| `finishReason` | 终止块 | `stop` / `length` |

- **没有** `id`、`model`、`usage`、`[DONE]`。
- **没有 charset**：`Content-Type: text/event-stream` 不含 `charset=utf-8`。任何按 HTTP 规范
  默认 ISO-8859-1 解码的客户端都会把中文解成 `æä»¬`。**必须强制 UTF-8。**
- 分块很碎（约 1.7 字符/块），需要按行缓冲而不是按块处理。

## 6. 错误响应

Spring Boot 默认错误结构：

```json
{"timestamp":"2026-10-29T14:22:39.652+00:00","status":400,"error":"Bad Request","path":"/chatCompletion"}
```

| 码 | 触发 |
| --- | --- |
| `400` | 缺 `aibsg` |
| `500` | 签名与 body 不匹配 / `content` 是数组 / 缺 `messages` |
| `503` | 后端过载（并发高峰出现，实测 10 并发短请求即可触发） |
| `404` | 其它任何路径 |

## 7. 能力边界（实测，勿假设）

| 项 | 值 |
| --- | --- |
| 模型数量 | 1 个（`aibmd` 被忽略，24 个候选模型名行为一致） |
| 模型画像 | 自述 Qwen 系、知识截止 2020、7B 级；中文可用，代码能力尚可，长文末尾复读 |
| 有效上下文 | **~600–1000 汉字**（613 字符 needle 命中，1013 字符即失效） |
| 单次输出上限 | ~3489 汉字 → `finishReason: length` |
| 采样参数 | 全部无效 |
| 多模态 | 不支持（content 数组直接 500） |
| 吞吐 | 长文本 ~175 字符/秒；TTFT 2–7 秒（经大陆代理） |
| 并发 | ≤5 舒适，10 排队，16 出现流中断 |
| 请求体大小 | 400 KB 也照收，不返回 413 |

## 8. 最小可运行示例（Python，仅标准库 + cryptography）

```python
import base64, hashlib, json, requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

PUB = "<第 2 节的 base64>"
URL = "https://aibr.elway-mobile.com/chatCompletion"
KEY = serialization.load_der_public_key(base64.b64decode(PUB))

def ask(text, timeout=(20, 180)):
    body = json.dumps({"messages": [{"role": "user", "content": text}]},
                      ensure_ascii=False, separators=(",", ":")).encode()
    aibsg = base64.b64encode(KEY.encrypt(hashlib.md5(body).digest(), padding.PKCS1v15())).decode()
    r = requests.post(URL, data=body, stream=True, timeout=timeout,
                      headers={"aibsg": aibsg, "Content-Type": "text/event-stream"})
    r.encoding = "utf-8"                       # 必须，否则中文乱码
    out = []
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        chunk = json.loads(payload)
        if chunk.get("content"):
            out.append(chunk["content"])
        if chunk.get("finishReason"):
            break
    return "".join(out)

print(ask("你好"))
```

## 9. 相关情报

- Firebase 远程配置（可匿名 fetch，project `ew-ask-eva`）：
  `ai_bridge_model=t1`，`ai_bridge_url` 指向一个 AWS Lambda Function URL。
- 该 Lambda 是**空实现占位**：任意 method/path/body（含 1MB 垃圾二进制、非法 UTF-8、畸形 JSON）
  一律 `200 + application/octet-stream + 0 字节`。拿不到任何服务，流量仍全走 `aibr`。
- 客户端"token 余额"（1000 免费 / 0.3 系数 / 看广告 / 内购）纯本地 SharedPreferences 记账，
  服务端既不接收也不校验 → 与能否调用无关。
