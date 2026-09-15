# FLY FUND contracts — v0.1

本目录实现 BSC 上的 **Fund 金库**。$FLY 仍由 Flap 发行；这里不另发同名币。

状态：源码、可重复编译、23 项本地 EVM/配置测试已完成。**未部署、未独立审计、未验证真实 Flap 代币及实际 DEX 池的兼容性。** 网站目前仍为演示，不会因为加入本目录而开始收款。

## 能做什么

| 功能 | 链上行为 |
| --- | --- |
| 投喂 | 用户先 approve，然后调用 `feed(amount, minReceived, message)`；FLY 留在 Fund |
| 记录 | `Fed` 日志记录投喂人、序号、申请数量、实际到账数量、文字及其哈希 |
| 收 BNB | 原生 BNB 可以直接转入 Fund，产生 `NativeReceived` 日志 |
| BNB 参与交易 | `wrapBNB()` 把金库 BNB 包装为 WBNB，WBNB 仍属于金库 |
| 兑换 | 执行地址调用 `swapExactTokens`，只走固定的 V2 Router 和固定资产白名单 |
| 留存所得 | Router 的接收人固定为 Fund；检查实际输入支出和输出到账 |
| 执行约束 | 单笔额度、每种输入资产的 UTC 日累计额度、最低输出、最远 10 分钟 deadline |
| 追踪与幂等 | 每次成功兑换关联非零 `decisionHash`；相同哈希不能再成功执行 |

FLY 与 WBNB 自动在白名单；BNCB、其他 BSC 资产需要在部署时提供核实过的合约地址。两段路径 `FLY → WBNB`、三段路径 `FLY → WBNB → BNCB` 均可，反向交易也可。不跨链。

**没有** owner、任意提款、任意代币授权、任意 call/delegatecall、救援提款、代理升级、修改 Router、增删白名单、修改执行地址或修改额度的接口。部署人没有额外权限。

这也意味着：当前版本不支持 LP 添加/移除、往 CZ/Giggle/私人地址转账、用户本金赎回、收益分红或者从金库空投资产。它完成的是收款与受限兑换，不能称为支持任意投资动作的钱包。

## 必须准确描述的控制权

- Fund 地址是持币的合约地址；执行地址是支付 Gas、触发允许交易的另一地址。
- 执行密钥仍有人或服务保管。合约无法鉴别“Fly 自己的判断”与人工签名。`decisionHash` 仅是可对应公开决策记录的哈希，不是 AI 证明。
- 可以说“取消团队任意提款入口、交易所得留在金库”；不能据此说“完全不可 rug”“没有任何人为控制”或“本金安全”。
- 执行者选择的 `minAmountOut` 没有独立价格预言机约束。恶意执行者仍可通过糟糕价格、被操纵的允许资产池、与外部地址配合交易造成经济损失。额度限制的是资产支出，不是净值损失，也不是反 MEV 保证。
- 固定地址的外部 token/router 若可升级或有黑名单、税率修改等权限，仍构成额外信任边界。
- 固定执行地址若为丢失私钥的 EOA，将无法再交易。若使用可轮换签名人的智能账户，轮换发生在该账户内，其对金库的权限仍受本合约限制。账户方案需在部署前确定。
- 本合约不可升级，没有向后续版本整体迁移资产的接口。新增资产/投资动作不能在原金库临时加上。**部署前必须接受这个边界；尚未确定时不要收真币。**

## 记账口径

`totalFed` / `fedBy` 是成功 `feed()` 的历史 FLY 净流入；不是当前余额、收益、份额、所有权或可赎回额度。余额由各 token 的 `balanceOf(Fund)` 查询，BNB 用原生余额查询。

支持扣税后净到账的常规 fee-on-transfer token，以及不返回 bool 的旧式 token。收到 0、超过请求金额或低于用户最低到账量会回滚。Rebase/Reflection token 不在支持范围。

直接给合约转 ERC20 不会触发 `Fed`、不会产生文字或投喂权重，也不能追回误转资产。BNB 收款也不产生 FLY 权重。只有受支持资产可兑换。

文字是公开的 calldata 和事件数据，不是私密聊天；最大 **1024 UTF-8 字节**（常见中文约 341 字），前端需用 `TextEncoder` 检查字节长度并以普通文本渲染。原始文字保留在事件，不额外重复占用链上字符串存储。

## 额度规则

部署时明确填写 `maxTradeBps` 与 `dailySpendBps`，`10_000 = 100%`，不可事后修改。本仓库没有默认生产额度，示例配置刻意留空。

测试采用 **10% 单笔 / 25% 每日**，只是测试值。假设当天第一次卖 FLY 前金库有 10,000 单位 FLY：

1. 第一笔最多卖 1,000。
2. 卖完后余额 9,000，下一笔最多 900。
3. 当天累计最多卖 2,500；后续投喂或买回不增加当天 FLY 额度。
4. 下一 UTC 日首次卖 FLY 时，按当时余额重新计算。不同输入资产分别记账。

这是 UTC 日额度，不是滚动 24 小时额度；跨 UTC 午夜可以先后使用两天额度。已失败并回滚的兑换不消耗额度或 decisionHash。

## 编译和本地验证

独立 Node 工具链，不修改网站依赖。推荐 Node 22 LTS。本次测试环境 Node 25，Ganache 原生加速不兼容时自动使用 JS 实现，23 项测试均通过。

```sh
cd fly-fund/contracts
npm ci --prefer-offline --no-audit --no-fund
npm run build
npm test
```

固定 Solidity `0.8.30`、OpenZeppelin `5.4.0`，optimizer 200，EVM `paris`。`package-lock.json` 固定依赖。主合约 runtime bytecode 6,107 字节。产物在忽略的 `artifacts/`：ABI、bytecode、完整编译输入 `standard-input.json`（含依赖，可用于浏览器验证）。

测试覆盖真实本地交易余额变化、税币/旧式 token、重入、越权、实际回滚、兑换接收人、授权清零、每日额度跨日和分拆、幂等、构造参数和错误网络。使用 mock Router/token，**不是 BSC 主网 fork 测试，也没有真实链上 receipt**。

## 部署准备

先复制 `deploy.example.json`，填写已核实的 FLY、WBNB、Router、执行地址、允许的额外资产及额度。`chainId` 必须为 56 或 97。所有代币与 Router 必须已经部署，因此流程是先发行 FLY，再部署 Fund；如果 Flap 发行时必须预填最终 Fund 地址，需要另行确定预计算地址/创建流程，不能随便填临时地址。

```sh
# 通过环境提供只读 RPC；不用部署私钥。
npm run prepare -- /absolute/path/to/config.json
```

该命令要求环境中已有 `BSC_RPC_URL`。它核对 RPC 链、各地址存在代码、Router 的 WETH()/factory()，读取代币单位，再输出 `artifacts/deployment-plan.json`，包括构造参数、完整编译输入、initCodeHash、**未签名**部署交易。它从不签名或广播，也不会输出 RPC 凭证。

部署前还需要实际资产的 fork 验证：approve/feed、税率、FLY → WBNB、WBNB → FLY/BNCB、失败回滚与确认后的余额。地址有代码或单测通过不能替代这些检查。

当前兑换 ABI 对应 PancakeSwap V2 风格 Router；**不支持 Flap 内盘 Portal、V3 或 Universal Router**。必须先检查实际 token 的迁移状态、DEX 类型与有效池子。若 FLY/BNCB 未迁移或不在 V2，应先调整实现，不能把别的 Router 地址硬填进去。

参考：[PancakeSwap V2 Router](https://docs.pancakeswap.finance/to-delete/smart-contracts/pancakeswap-exchange/v2-contracts/router-v2)、[Flap 代币状态与 DEX 类型](https://docs.flap.sh/flap/developers/inspect-a-token)、[Flap Tax Token V2](https://docs.flap.sh/flap/developers/flap-tax-token/tax-token-v2)、[OpenZeppelin SafeERC20](https://docs.openzeppelin.com/contracts/5.x/api/token/erc20#SafeERC20)。

## 前端 / Fly 后台接入

用户流程：确认链与地址 → 读取 decimals → 精确金额 approve → `feed(amount, minReceived, message)` → 等待成功 receipt → 解码 `Fed` → 显示实际到账。签名成功不等于收款成功。拒签/回滚不能写成已投喂。

索引器用 `(chainId, contract, transactionHash, logIndex)` 去重，并处理链重组。把收到的记录送给 Fly 时使用 `receivedAmount`，不使用申请数量。前端可展示 block/tx 链接；生成链下文本不能伪造成链上喂食。

Fly 后台流程：收集投喂 → 构建并保存决策记录 → 生成包含链、Fund、动作参数和唯一决策 ID 的 `decisionHash` → 报价并计算税后 minOut → 检查额度 → 固定 Router swap → 等待 receipt / 确认 → 对账。提交状态不明时，先按签名交易哈希查清结果，不用新 decisionHash 重发。

ABI 入口：`feed`、`wrapBNB`、`swapExactTokens`、`totalFeeds`、`totalFed`、`fedBy`、`allowedAsset`、`dailyBudget`、`executedDecision`、`executor`、`router`、`wrappedNative`、`maxTradeBps`、`dailySpendBps`。事件为 `Fed`、`NativeReceived`、`NativeWrapped`、`Swapped`。
