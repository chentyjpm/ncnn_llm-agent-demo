# H5 服务和 HTTP API

入口：`python -m local_agent serve --config configs/local.json --open`。原生 H5，无前端构建步骤。服务仅监听 `127.0.0.1`；这是单用户本机原型，不是生产公网服务。

## 请求与保护

页面先调用 `GET /api/bootstrap` 获取本次服务的随机 token 和脱敏运行状态。其余 API 使用 `X-Agent-Token` 请求头。token 不进入 URL，不写入 localStorage，也不用于远程服务身份管理。

服务拒绝非本机 Host、外源 Origin 和跨站 Fetch 请求。API 写请求必须是有长度上限的 `application/json`；不开放 CORS。静态路径仅允许固定 HTML/JS/CSS 文件，不暴露源代码目录、模型目录或会话数据目录。

## API 表

| 方法 | 路径 | 输入/输出 |
|---|---|---|
| GET | `/api/bootstrap` | token、runtime |
| GET | `/api/runtime` | 模型名、后端、设备、存在性检查、功能开关，不返回任意命令/凭证 |
| GET / POST | `/api/sessions` | 列表 / 创建空会话（POST `{}`） |
| GET | `/api/sessions/<id>` | 会话与完整已存储消息 |
| PATCH | `/api/sessions/<id>` | `{"title":"名称"}`，最多 80 字符 |
| DELETE | `/api/sessions/<id>` | 删除历史 JSON，保留工作文件和审计日志；活跃会话拒绝删除 |
| POST | `/api/sessions/<id>/messages` | message、mode、max_new_tokens、attachments，返回 run_id 和当前会话 |
| GET | `/api/runs/<id>/events?after=0` | events、cursor、status；最多等待 15 秒，完成事件可重读 |
| POST | `/api/runs/<id>/cancel` | `{}`，合作取消，不回滚已完成操作 |
| POST | `/api/runs/<id>/approve` | approval_id 和布尔 allow；只用于当前待确认调用 |
| GET | `/api/sessions/<id>/files?path=.` | 当前工作区一层目录内容 |
| POST | `/api/sessions/<id>/upload` | name、base64 data，最多 8 MiB，随机前缀命名 |
| GET | `/api/sessions/<id>/download?path=…` | 最大 16 MiB 的二进制附件，不以内联 HTML 执行 |

错误响应为非 2xx 状态与 `{"error":"诊断"}`。503 表示模型未配置，409 表示任务占用、无效审批或状态冲突，403 表示请求源/token 被拒绝。没有 `/v1/chat/completions` 等 OpenAI 兼容接口。

示例消息：

```json
{
  "message": "列出当前工作区文件",
  "mode": "agent",
  "max_new_tokens": 512,
  "attachments": []
}
```

## 运行状态机

`running → completed / failed`；取消先变成 `stopping`，工作线程确认后进入 `cancelled`。无论页面是否打开，已提交任务都属于本次本地服务进程；断开浏览器不会自动撤销任务。服务重启不能恢复模型执行，会将遗留 running 消息标为失败。

每个 Run 有独立 ID、事件游标、取消标志和待确认请求。`approval_required` 携带完整工具参数；`approval_resolved` 说明该次是否获准；`done` 携带最终消息。当前是**事件长轮询**，不是 token streaming/SSE/WebSocket。

网页刷新时读取会话的 active_run，再从事件 0 重放，恢复进行中的工具记录与授权卡。最多保留 32 个已结束 Run 的内存事件；会话、最终 trace 和 JSONL 日志另外保存在磁盘。

## 目录与限制

- 会话：`web-data/sessions/<id>.json`，原子替换；最多 200 会话、每会话 50 轮。
- 审计：`web-data/runs/<run_id>.jsonl`，包含模型原文、工具参数/结果等敏感信息。
- 工作区：`<config.workspace>/web/<session_id>/`，和命令行的 workspace 根目录不同。
- 静态资源：`local_agent/webui/`，前端没有访问任意本机路径的 API。

仅一个服务进程、一个活跃任务；不在运行中上传以避免并发写。应用路径检查仍需要可信的单写者环境，不是内核沙箱。不要让多个实例共用 data-dir，不要把 data-dir 放进可供 Agent 写入的会话目录。

## 验证边界

`tests/test_web.py`：真实 HTTP/文件操作，标记的模型替身，验证权限和状态；`scripts/browser_web.py`：真实 Chromium，标记的模型替身，验证可见交互和布局；`scripts/web_real_smoke.py`：真实 ncnn CPU，通过 HTTP 连续问答，无测试替身。三者分别出报告，不能互相冒充。

基础依据：[Python http.server 文档](https://docs.python.org/3/library/http.server.html)、[MDN CSP connect-src](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/connect-src)。应用不是通用生产 Web 框架，CSP/同源检查不是完整安全认证。
