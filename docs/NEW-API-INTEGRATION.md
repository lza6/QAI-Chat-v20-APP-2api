# new-api 对接指南（QAI Chat v20.0 网关）

> 面向：在 `lza6/new-api-Max` 上落地 QAI 渠道的会话/工程师。
> 上游协议细节见 [PROTOCOL.md](PROTOCOL.md)；逆向结论见 [../ANALYSIS.md](../ANALYSIS.md)。
>
> **本文只描述"怎么接"，不替你改 new-api 代码。** 两条路径，选一条即可。

---

## 0. 先明确这条渠道的定位

这个上游**不适合做主渠道**，接之前请确认你接受以下事实（全部实测）：

| 事实 | 影响 |
| --- | --- |
| 只有一个后端模型，`aibmd`（模型名 header）被服务端忽略 | new-api 里配多个模型名指向它没有意义 |
| `max_tokens`/`temperature`/`top_p`/`stop` 全部无效，上游 DTO 只有 `messages` | 客户端设的参数会被静默丢弃，长回答不受控 |
| 有效上下文 **~600–1000 汉字**（1013 字符就丢信息） | 长对话/长文档一用就废 |
| 单次输出硬上限 ~3489 汉字，然后 `finish_reason=length` | 长文生成不可用 |
| 舒适区 ≤ 5 并发；16 并发出现流中断；短请求高峰会 503 | 必须限流/低权重 |
| 上游在中国大陆**不可直连**（TCP 阻断），必须经代理 | 网关或 new-api 出口必须能出海 |
| 无鉴权、无配额、无 `usage` | 计费只能估算或按次 |

---

## 1. 两条对接路径

| 路径 | 工作量 | 适用 |
| --- | --- | --- |
| **A. OpenAI 兼容渠道 → 指向本仓库的 Python 网关** | 零代码，Web UI 点几下 | 推荐。快速验证、低频自用 |
| **B. 在 new-api 里写原生 Go 渠道** | 改 5 个文件 + 写 2 个 go 文件 | 不想多跑一个 Python 进程、要纳入 new-api 的渠道池/代理/重试体系 |

两条路径的上游行为完全一致，差异只在"签名和 SSE 转换发生在哪"。

---

## 2. 方案 A：OpenAI 兼容渠道（零代码，推荐）

原理：`demo_server.py` 已经把这套私有协议包装成标准 OpenAI 接口，new-api 只需要把它当成一个
普通 OpenAI 上游。

### 2.1 部署网关并保持常驻

网关必须跑在**能访问 `aibr.elway-mobile.com` 的机器**上（大陆机器需带 `HTTPS_PROXY`）。

```bash
# 依赖
pip install -r requirements.txt

# 前台试跑（先确认能出结果）
python demo_server.py --port 8931
curl -s http://127.0.0.1:8931/health
```

常驻（选一种）：

**Linux systemd**

```ini
# /etc/systemd/system/qai-gateway.service
[Unit]
Description=QAI v20 OpenAI-compatible gateway
After=network-online.target

[Service]
WorkingDirectory=/opt/qai2000
Environment=HTTPS_PROXY=http://127.0.0.1:10808
Environment=HTTP_PROXY=http://127.0.0.1:10808
Environment=DEMO_API_KEY=change-me-to-anything
ExecStart=/usr/bin/python3 demo_server.py --host 0.0.0.0 --port 8931
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now qai-gateway
```

**Windows（nssm）**

```powershell
nssm install QAIGateway "C:\Python314\python.exe" "D:\apk-analysis\qai2000\demo_server.py --port 8931"
nssm set QAIGateway AppDirectory "D:\apk-analysis\qai2000"
nssm set QAIGateway AppEnvironmentExtra "HTTPS_PROXY=http://127.0.0.1:10808"
nssm start QAIGateway
```

> ⚠️ 一定设置 `DEMO_API_KEY`。网关默认不校验 key，但一旦监听在 `0.0.0.0`，**任何能访问
> 这个端口的人都能白嫖你的代理出口**。

### 2.2 new-api 渠道字段

进入 **渠道 → 添加渠道**：

| 字段 | 填什么 | 说明 |
| --- | --- | --- |
| 类型 | **OpenAI**（`ChannelTypeOpenAI = 1`） | 走 openai adaptor，客户端路径 `/v1/chat/completions` 原样透传 |
| 名称 | `QAI-v20` | 任意 |
| 密钥 | 与 `DEMO_API_KEY` 相同（若网关未设，随便填非空） | new-api 会带 `Authorization: Bearer <key>`，网关按需校验 |
| 代理 | 留空 | 网关自己负责出海；**不要**让 new-api 再套一层代理 |
| **Base URL** | `http://<网关IP>:8931` | **不要带 `/v1`**。new-api 会用 `BaseURL + 客户端请求路径`，即 `http://host:8931/v1/chat/completions` ✅ |
| 模型 | `qai-v20` | 网关只认这一个 ID（其他 ID 也放行，但行为相同） |
| 分组 | 默认或自建的低优先级组 | 建议单独分组，避免被当主力 |
| 权重 | `1`（最低） | 定位于兜底 |
| 模型重定向 | `{"gpt-3.5-turbo":"qai-v20","qai-chat":"qai-v20"}` | 客户端想用常见名字时用它兜；**不要**重定向成 `gpt-4o`/`claude-*` 这类名字，会误导调用方 |

> 若 base_url 想带路径前缀（例如反代到 `/qai`），网关不支持子路径，直接用根路径反代。

### 2.3 验证（三层，逐层过）

```bash
# 第 1 层：网关直连（绕过 new-api）
curl -s http://<网关IP>:8931/v1/chat/completions \
  -H "Authorization: Bearer <DEMO_API_KEY>" -H "Content-Type: application/json" \
  -d '{"model":"qai-v20","messages":[{"role":"user","content":"reply exactly: L1-OK"}]}'

# 第 2 层：new-api 的渠道测试（后台网页 -> 渠道 -> 测试），应返回 L2 成功
# 第 3 层：走 new-api 的 token
curl -s http://<new-api>/v1/chat/completions \
  -H "Authorization: Bearer <new-api令牌>" -H "Content-Type: application/json" \
  -d '{"model":"qai-v20","stream":true,"messages":[{"role":"user","content":"reply exactly: L3-OK"}]}'
```

三层都过才算接通。只过第 1 层说明 new-api 侧配置错（90% 是 base_url 多写了 `/v1`）。

### 2.4 建议的渠道参数

```
权重        1（或 0，需要手动启用时才开）
优先级      最低
自动禁用     开（配合失败重试，别让它拖垮主链路）
重试        1～2 次（网关已内置一次 503 重试）
```

---

## 3. 方案 B：new-api 原生 Go 渠道

适用于不想多跑 Python 进程，或要把 QAI 纳入 new-api 的渠道池/代理池/统计体系。

### 3.1 现状锚点（在 `lza6/new-api-Max` 实测确认）

| 项 | 现状 | 你要做的 |
| --- | --- | --- |
| `constant/channel.go` | `ChannelTypeDuck = 73` 是当前最大 | 追加 `ChannelTypeQAI = 74` |
| 同文件 `channelTypeNames` map | 约 231 行（`ChannelTypeDuck: "Duck"`） | 追加 `ChannelTypeQAI: "QAI"`（后端渠道名表） |
| `controller/twoapi_panel.go:44` 的 `TwoAPIChannelTypes`（+ `controller/twoapi_hourly.go:32` 的同类集合） | 2API 渠道白名单 | 仅当你要走 2API 快路径（不读库、按"2 币"计费）时追加 `ChannelTypeQAI`；**普通渠道不要加** |
| `constant/api_type.go` | 末尾是 `APITypeDuck` + `APITypeDummy // do not add any channel after this` | 在 `APITypeDummy` **之前**插 `APITypeQAI`（iota 顺序敏感） |
| `relay/relay_adaptor.go` `GetAdaptor` | switch 注册各渠道 | 加 `case constant.APITypeQAI: return &qai.Adaptor{}`（记得加 import） |
| `common/api_type.go` `ChannelType2APIType`（约 104 行 `case constant.ChannelTypeDuck:`） | channel → apiType 的映射，**不在 relay_adaptor.go 里** | 加 `case constant.ChannelTypeQAI: apiType = constant.APITypeQAI` |
| `web/src/features/channels/constants.ts` `CHANNEL_TYPES` map（第 99 行 `73: 'Duck'`） | 前端渠道下拉的硬编码表 | 加 `74: 'QAI'` |
| 同文件 `CHANNEL_TYPE_DISPLAY_ORDER`（第 102-106 行，末尾是 `...71, 72`） | 前端下拉显示顺序 | 追加 `74`（漏加则下拉里看不到这个渠道） |
| `relay/channel/qai/` | 不存在 | 新建 `adaptor.go` + `do_request.go` |

> **最省事的做法**：`cp -r relay/channel/aivm relay/channel/qai`，然后把 adaptor 签名、URL、header、
> DoRequest/DoResponse 换成下面的实现。`aivm` 和 `duck` 都已经是"私有协议 + 自签头"的现成范本，
> `duck/do_request.go` 里已有 `crypto/rsa` 的用法可以对照。

### 3.2 参考实现（签名部分，可直接用）

`relay/channel/qai/do_request.go`：

```go
package qai

import (
	"crypto/md5"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/base64"
	"errors"
)

// APK 内 md.elway.evo.utils.Crypto 硬编码的 X.509 SubjectPublicKeyInfo，2048 位。
const publicKeyB64 = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA7TIHog89K/IEIO3LF2/B9A8Gi/DkXZG6" +
	"Y+PoydoKD9JrxR9CHRP1Pmbsm09oY9ycxyw7Tfb4ljt9ySxOmzMALoN1mJx0ontUe9+BoAxddK2NQ" +
	"DhRz55B/HUUAGFJ9tGTC8dlkAgc0OnEBR9O60x5KDOltgNR/iriGdlxMoTGRnGS6LOXGiRQlAzyj1" +
	"hmyTl9vOePkWTvj/GG3W1sWaw9LTvd83WZdxvgGtcjMe4ZqW86BGTmG32yqgg+uQCew88BaSRAmTx" +
	"pH/YqKWsBD7mk5pS80+ss2mS6XeQRtqN82DActjrgOoOiaL9nZdh7DZJIY2Sqz8aHh9vkHeqwJi1i" +
	"DwIDAQAB"

// signBody 复刻 Crypto.encrypt(): RSA/ECB/PKCS1Padding(MD5(rawBody))，Base64 标准编码。
// 注意语义是「加密摘要」而不是标准签名；服务端解密后比对 MD5。
func signBody(body []byte) (string, error) {
	der, err := base64.StdEncoding.DecodeString(publicKeyB64)
	if err != nil {
		return "", err
	}
	parsed, err := x509.ParsePKIXPublicKey(der)
	if err != nil {
		return "", err
	}
	pub, ok := parsed.(*rsa.PublicKey)
	if !ok {
		return "", errors.New("qai: embedded key is not RSA")
	}
	digest := md5.Sum(body) // [16]byte
	sig, err := rsa.EncryptPKCS1v15(rand.Reader, pub, digest[:])
	if err != nil {
		return "", err
	}
	return base64.StdEncoding.EncodeToString(sig), nil
}
```

### 3.3 参考实现（请求 DTO 与 adaptor 骨架）

```go
package qai

const ChannelName = "QAI"
const defaultBase = "https://aibr.elway-mobile.com"

// 上游只接受这两个字段。任何 max_tokens/temperature 都必须在这里被丢弃。
type qaiMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type qaiRequest struct {
	Messages []qaiMessage `json:"messages"`
}

func (a *Adaptor) GetModelList() []string   { return ModelList }
func (a *Adaptor) GetChannelName() string   { return ChannelName }
func (a *Adaptor) Init(info *relaycommon.RelayInfo) {}

// 上游路径是 /chatCompletion，与 OpenAI 的 /v1/chat/completions 不同，必须硬编码。
func (a *Adaptor) GetRequestURL(info *relaycommon.RelayInfo) (string, error) {
	base := strings.TrimRight(info.ChannelBaseUrl, "/")
	if base == "" {
		base = defaultBase
	}
	return base + "/chatCompletion", nil
}

func (a *Adaptor) SetupRequestHeader(c *gin.Context, req *http.Header, info *relaycommon.RelayInfo) error {
	req.Set("Content-Type", "text/event-stream")
	req.Set("Connection", "keep-alive")
	req.Set("Accept", "text/event-stream")
	return nil
}

// ConvertOpenAIRequest: dto.GeneralOpenAIRequest -> qaiRequest。
// 常见坑：content 可能是 string，也可能是 []part，必须把数组拼成字符串（上游只收 string）。
func (a *Adaptor) ConvertOpenAIRequest(c *gin.Context, info *relaycommon.RelayInfo,
	request *dto.GeneralOpenAIRequest) (any, error) {

	out := qaiRequest{Messages: make([]qaiMessage, 0, len(request.Messages))}
	for _, m := range request.Messages {
		content, err := m.Content.(string) // 若为 []any：遍历取 type=="text" 的 text 拼接
		if !err {
			content = flattenContentParts(m.Content)
		}
		out.Messages = append(out.Messages, qaiMessage{Role: m.Role, Content: content})
	}
	return &out, nil
}
```

`DoRequest` 的关键点（读取 `requestBody` → 签名 → 自己发请求 → 返回 `*http.Response`）：

> new-api 的约定：`Adaptor.DoRequest` 返回的 `any` 会被框架当 `*http.Response` 用，
> 随后交给 `Adaptor.DoResponse` 处理。私有协议渠道（如本仓库已有的 `duck`、`aivm`）都是
> 在 `DoRequest` 里自己 `http.NewRequest` + `http.Client.Do`，**不走** `channel.DoApiRequest`
> ——因为那条路径拿不到签名所需的 body 字节。

```go
func (a *Adaptor) DoRequest(c *gin.Context, info *relaycommon.RelayInfo, requestBody io.Reader) (any, error) {
	raw, err := io.ReadAll(requestBody)
	if err != nil {
		return nil, err
	}
	sig, err := signBody(raw) // 必须对「即将发送的原始字节」签名，差一个空格就 500
	if err != nil {
		return nil, err
	}
	url, err := a.GetRequestURL(info)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(c.Request.Context(), http.MethodPost, url, bytes.NewReader(raw))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "text/event-stream")
	req.Header.Set("Accept", "text/event-stream")
	req.Header.Set("Connection", "keep-alive")
	req.Header.Set("aibsg", sig)
	req.Header.Set("aibmd", "t1") // 服务端忽略，保留只为贴近原客户端

	client := &http.Client{Timeout: 180 * time.Second}
	// 如需走渠道代理，按本仓库 duck/aivm 的现有做法挂 Transport，或对齐 common.SetEgressProxy
	if proxy := info.ChannelSetting.Proxy; proxy != "" {
		// transport := ... 参考 relay/channel/duck/do_request.go 的 clientFor(proxy)
		_ = proxy
	}
	return client.Do(req)
}
```

`DoResponse` 的关键点（上游 SSE → OpenAI chunk）：

上游是 `data:{"content":"x"}` / `data:{"finishReason":"stop"}`，**不是** OpenAI 格式：

```go
scanner := bufio.NewScanner(resp.Body)
scanner.Buffer(make([]byte, 0, 64*1024), 1<<20)
for scanner.Scan() {
	line := scanner.Text()
	if !strings.HasPrefix(line, "data:") {
		continue
	}
	payload := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
	if payload == "" {
		continue
	}
	var chunk struct {
		Content      string `json:"content"`
		FinishReason string `json:"finishReason"`
	}
	if err := json.Unmarshal([]byte(payload), &chunk); err != nil {
		continue // 上游偶发半包/空行，不能整条流崩掉
	}
	if chunk.Content != "" {
		// 写 openai chunk: choices[0].delta.content
	}
	if chunk.FinishReason != "" {
		// 写终止 chunk: choices[0].finish_reason = "stop"|"length"，然后跳出
	}
}
// 最后补 usage（上游无 usage，需估算，参考 demo_server.py 的 estimate_tokens 中文/ASCII 分支）
```

> ⚠️ **以上 Go 代码是参考骨架，未在 new-api 仓库内编译验证。** 落地时必须按
> `relay/channel/duck`（自签头 + 自建 client 的完整范本）和 `relay/channel/aivm`
> （最简结构）的实际签名与 helper 对齐，并以 `go build ./...` 通过为准。

### 3.4 编译与验收

```bash
cd new-api
cd relaykit && GOWORK=off go build ./... && cd ..   # 本仓库约定：relaykit 必须独立可构建
go build ./...
go vet ./relay/channel/qai/...
# 起服务后：后台渠道测试 -> 三层验证同 §2.3
```

---

## 4. 必须在 new-api 侧做的降级处理

这些不是可选项，是"别让用户以为能用"的必要动作：

1. **模型名不要伪装**。别把 `qai-v20` 重定向成 `gpt-4o`。这个上游是 7B 级中文模型，冒充会误导。
2. **倍率拉到极低**（建议 0.1 或按次 0.5）。上游无 `usage`，计数是估算的，按高价卖等于欺骗。
3. **限制入参长度**。new-api 侧无法强制 `max_tokens`，但可以在路由前置校验：`len(prompt) > 1500 字符`
   直接拒绝（因为超了必然丢信息）。
4. **并发闸门**。上游舒适区 5 并发，建议该渠道单独限流（`渠道限流` 或分组 RPM），别让它和其他
   渠道共享池子。
5. **关闭该渠道的 `function calling` / 多模态**。上游不支持 tools、不支持 image parts
   （content 数组会直接 500）。

---

## 5. 计费建议

上游不返回 `usage`，网关的 `usage.prompt_tokens/completion_tokens` 是估算（响应里
`"estimated": true` 明确标注）。因此：

- 优先 **按次计费**（`模型倍率` 设固定价）；
- 若要按 token 计费，接受误差（估算公式：CJK 1 字符 ≈ 1 token，ASCII 4 字符 ≈ 1 token）；
- **不要把 estimated usage 当真实账单依据**对外结算。

---

## 6. 排障表

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| new-api 渠道测试 404 | Base URL 多写了 `/v1` | 改成 `http://host:8931` |
| 401 from gateway | 网关设了 `DEMO_API_KEY`，new-api 密钥不一致 | 两边对齐 |
| 502 `upstream unreachable` | 网关所在机器无法出海 | 给网关配 `HTTPS_PROXY`，或换机器 |
| 中文变 `æä»¬` | 读流时没强制 UTF-8 | 网关已处理；自写渠道需 `resp.Encoding` 或用 UTF-8 scanner |
| 上游 400 | 缺 `aibsg` | 签名没带上 |
| 上游 500 | 签名与 body 不匹配 / content 是数组 / 缺 messages | 对**最终发送的字节**签名；content 必须是 string |
| 上游 503 | 后端过载 | 降并发、重试（网关已内置一次） |
| 回答到一半停住且 `finish_reason=length` | 上游硬上限 ~3489 汉字 | 正常行为，不是 bug |
| 长对话答非所问 | 有效上下文仅 ~1k 汉字 | 在 new-api 侧拒绝超长 prompt |
| 端口被占 / 启了两个实例 | Windows `SO_REUSEADDR` 双绑定 | 见 ANALYSIS.md §9.7 |

---

## 7. 明确不要做

- ❌ 公开转售、批量注册、对外当产品卖——上游是他人运营的白标 SaaS，无鉴权≠授权
- ❌ 把它配成主力/高权重渠道
- ❌ 在 README/宣传里宣称它支持 GPT-4/Claude 或长上下文
- ❌ 把 `estimated: true` 的用量当真实账单
