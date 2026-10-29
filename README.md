# QAI Chat v20.0 → OpenAI 兼容网关（逆向 R1）

对 `chat.gpt.open.ai`（应用名 **QAI Chat** v20.0 / versionCode 2001，Elway「EVO」白标模板）的
完整协议还原与本地网关落地。上游是 Spring Boot + nginx 的单一端点，鉴权仅靠一枚内置在 APK 里的
RSA 公钥签名，**无账号、无设备绑定、无配额头**。

- 协议还原：`qai_upstream.py`（签名 + SSE 解析，零 APK、零真机、零 Frida）
- 网关：`demo_server.py`（OpenAI `/v1/chat/completions`，流式/非流式）
- Demo 页：`demo.html`（浏览器实测通过）
- 压测：`stress.py`｜测试：`test_demo.py`｜浏览器验收：`browser_test.py`
- 深度分析：**[ANALYSIS.md](ANALYSIS.md)**
- `research/`：逆向过程中的一次性探针脚本（模型枚举、端点扫描、needle 测试等），仅作证据保留

## 快速开始

```powershell
pip install -r requirements.txt
python demo_server.py --port 8931          # 另开一个窗口
python test_demo.py --port 8931            # 20 项断言
python browser_test.py                     # Playwright 真实浏览器
python stress.py --target upstream --levels 1,5,10 --per-level 8
```

打开 <http://127.0.0.1:8931/> 就是 demo 页。

## 直接用（不启网关）

```python
import qai_upstream as up

text, finish = up.chat_completion([{"role": "user", "content": "你好"}])
print(text, finish)
```

## OpenAI 兼容调用

```bash
curl http://127.0.0.1:8931/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qai-v20","stream":true,
       "messages":[{"role":"user","content":"用一句话介绍杭州"}]}'
```

非流式返回标准 `chat.completion` 对象；流式返回 `chat.completion.chunk` + `data: [DONE]`。

## 上游的三个硬限制（不是网关的 bug）

| 现象 | 原因 |
| --- | --- |
| `max_tokens` / `temperature` / `stop` / `top_p` 全部无效 | 上游 DTO 只有 `messages` 一个字段，其余被丢弃。网关会把这些字段名回填到 `x_qai_ignored_params` 里明示 |
| 输出到约 3.5k 字符被截断 | 上游有自己的硬上限，`finish_reason` 会返回 `length` |
| 换 `model` 名没有任何区别 | `aibmd` header 被服务端忽略，只有一个后端模型 |

## 模型

上游只有一个模型（远程配置别名 `t1`，自述 Qwen 系、知识截止约 2020、中文可用、代码能力尚可）。
不要按 GPT-4/Claude 的预期使用它，详见 `ANALYSIS.md` 的能力测绘。

## 免责声明

本项目仅用于协议研究与自用网关验证。上游服务归其运营方所有，请勿用于商业转售或大规模滥用；
由此产生的任何后果由使用者自负。

仓库内不含任何私有凭据：出现的 RSA 公钥、Firebase `google_api_key`、AdMob 单元 ID 均为
**客户端公开信息**（它们本来就以明文存在于任何一份该 APK 里），不构成可轮换的秘密。APK 本体与
反编译产物已 `.gitignore`。
