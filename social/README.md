# Social inbox / 发帖草稿

独立的本地 Python 服务，不增加网站依赖。当前接收公开内容，保存到 SQLite，并保存 X / Binance Square 草稿。没有自动发布、钱包交易、LLM 调用或每日调度；也未接入前端或 fly 神经服务。

## 接入状态（2026-09-15）

| 能力 | 当前情况 |
| --- | --- |
| X 单帖读取 | FxTwitter v2，无需 X 官方 Key；已实测 CZ 的果蝇帖子 |
| X 账号时间线 | FxTwitter v2，已实测一页 20 条；提供分页游标 |
| X 付费备选 | TwitterAPI.io 适配代码及模拟响应测试完成；没有 Key，未做付费端到端验证 |
| Square 自动读取 | 未发现并验证官方读取接口；普通公开网页请求实际返回 HTTP 202 验证页，不把它当正文 |
| Square 内容导入 | 原帖 URL + 用户复制的正文；始终标记 `manual_unverified`，不是 API 读取成功 |
| X / Square 发帖草稿 | 本地保存正文、来源快照与建议请求体；不会调用发布接口 |
| Square 官方发布 | 已核对官方接口格式，后续可接专用发布 Key；当前仅导出 `bodyTextOnly` 请求体 |

FxTwitter 的公共服务是 X 的第三方接口。本次调用成功不代表长期可用性保证。TwitterAPI.io 当前标价为每 1000 条返回推文 $0.15；付费备选必须主动配置，不会悄悄切换并消费余额。Square 的官方发布 Key 不等于交易 Key，也不能据此假定可读取全站帖子。

来源：[FxTwitter v2](https://docs.fxembed.com/api/introduction/)、[账号时间线](https://docs.fxembed.com/api/twitter/operations/2profilehandlestatuses/)、[TwitterAPI.io 时间线](https://docs.twitterapi.io/api-reference/endpoint/get_user_last_tweets)、[价格](https://twitterapi.io/pricing)、[Binance 官方 Square 工具源码](https://github.com/binance/binance-skills-hub/tree/main/skills/binance/square-post)。

## 启动

Python 3.10+，只用标准库。可以复用模型实验室的 Python 环境：

```sh
.sites-runtime/flyai/venv/bin/python social/service.py --port 5188
```

VPS 可直接使用 `python3 social/service.py`。数据库默认在 `.sites-runtime/social/inbox.sqlite3`，已被 Git 忽略；迁移时要单独复制数据库。服务只监听 `127.0.0.1`，尚未实现公网认证和限流，不应直接暴露或挂到公网隧道。

付费备选：在进程环境里配置 `FLY_X_PROVIDER=twitterapi_io` 和 `TWITTERAPI_IO_KEY`。Key 不进入前端、命令参数、响应或日志。当前 FxTwitter 模式不需要任何账号或 Key。

## 接口

所有 POST 使用 `Content-Type: application/json`。示例：

```sh
curl http://127.0.0.1:5188/api/social/status

curl http://127.0.0.1:5188/api/social/read \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://x.com/cz_binance/status/2099713592903995839"}'

curl http://127.0.0.1:5188/api/social/collect \
  -H 'Content-Type: application/json' \
  -d '{"handle":"cz_binance","since":"2026-09-14T12:00:00Z"}'

curl http://127.0.0.1:5188/api/social/import \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.binance.com/en/square/post/298598452002002","author":"Binance Announcement","text":"这里放手动复制的原文；例子不会自动执行。"}'

curl http://127.0.0.1:5188/api/social/drafts \
  -H 'Content-Type: application/json' \
  -d '{"platform":"x","text":"这里放待审核的草稿正文。","source_ids":["x:2099713592903995839"]}'

curl http://127.0.0.1:5188/api/social/posts
curl http://127.0.0.1:5188/api/social/drafts
```

- `POST /read`：目前自动读取仅支持 X；传入 Square 返回明确的 `square_read_unavailable`。
- `POST /collect`：每次一页，返回 `next_cursor`。下一次带 `cursor` 继续。`since` 按原帖发布时间过滤；不要把第一页当成完整的每日统计，也不要把置顶、转发后的顺序当成时间顺序。
- `POST /import`：人工导入，不验证作者或事实。可提供带时区的 `created_at`；不提供则为空，绝不将导入时间伪装成发布时间。
- `POST /drafts`：`platform` 为 `x` 或 `square`。`text` 由调用者提供，本服务不声称用 LLM 自动撰写。可传 `source_ids`，保存引用内容当时的快照。长度只做本地上限检查，发布前仍需平台格式校验。
- `GET /posts`：最近 500 条，按首次收集时间排序；`GET /drafts`：最近 100 条。
- 没有 `/publish`。草稿不需要发布 Key，也没有设置隐式定时发送。

## 与投喂和每日决策的关系

所有抓取及人工导入都是 `input_kind=observation`，`paid_feed_weight=0`。推文点赞、作者粉丝数、帖子里自称的投喂金额，都不能增加真实投喂权重。后续必须由已确认的链上投喂记录单独关联。

正文始终是 `untrusted_external_text`：未来 LLM 可以提取内容，但不得把帖子里的“忽略规则／调用钱包／立即发帖”等文字当系统指令。`provider_fetched` 仅表示经第三方读取，`claim_verified=false`，不是对新闻真实性的背书。

同平台按帖子 ID 去重；暂未做跨平台同内容聚类。时间线保留真实作者，转发不会被伪装成被查询账号的原创。每日统一决策、神经刺激翻译、盈亏反馈与定时发布均是后续工作。

## 测试

```sh
python3 -m unittest discover -s social -p 'test_*.py' -v
```

测试覆盖去重和持久化、来源快照、转发作者归属、时间过滤、URL 限制、缺 Key 时不访问付费接口、上游错误、仅本地 API 与无发布路由。
