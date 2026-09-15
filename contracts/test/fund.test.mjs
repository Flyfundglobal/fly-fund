import test from 'node:test';
import assert from 'node:assert/strict';
import ganache from 'ganache';
import { BrowserProvider, ContractFactory, ZeroAddress, ZeroHash, id, toUtf8Bytes, keccak256 } from 'ethers';
import { compile } from '../scripts/compile.mjs';

const { output } = compile({ tests: true });
const artifact = name => Object.values(output.contracts).find(file => file[name])?.[name];
const sent = async promise => (await promise).wait();

async function fixture(t, { tokenType = 'MockToken', tradeBps = 1000, dayBps = 2500 } = {}) {
  const rpc = ganache.provider({ logging: { quiet: true }, chain: { chainId: 97, hardfork: 'shanghai' }, wallet: { totalAccounts: 4 } });
  t.after(() => rpc.disconnect());
  const provider = new BrowserProvider(rpc);
  provider.pollingInterval = 10;
  const [owner, executor, feeder, outsider] = await Promise.all([0, 1, 2, 3].map(i => provider.getSigner(i)));
  async function deploy(name, args = []) {
    const compiled = artifact(name);
    const instance = await new ContractFactory(compiled.abi, compiled.evm.bytecode.object, owner).deploy(...args);
    await instance.waitForDeployment();
    return instance;
  }
  const token = await deploy(tokenType);
  const wrapped = await deploy('MockWrapped');
  const extra = await deploy('MockToken');
  const router = await deploy('MockRouter');
  const args = [token.target, executor.address, router.target, wrapped.target, [extra.target], tradeBps, dayBps];
  const fund = await deploy('FlyFund', args);
  await sent(token.mint(feeder.address, 100_000n));
  await sent(token.connect(feeder).approve(fund.target, 100_000n));
  await sent(wrapped.mint(router.target, 100_000n));
  await sent(extra.mint(router.target, 100_000n));
  const feed = (amount = 10_000n, min = amount, message = 'BNB 牛逼') => fund.connect(feeder).feed(amount, min, message);
  const deadline = async (offset = 300) => Number((await rpc.request({ method: 'eth_getBlockByNumber', params: ['latest', false] })).timestamp) + offset;
  let sequence = 0;
  const swap = async (amount = 1000n, min = amount, opts = {}) => fund.connect(opts.signer ?? executor).swapExactTokens(
    amount, min, opts.path ?? [token.target, wrapped.target], opts.deadline ?? await deadline(), opts.hash ?? id(`decision-${++sequence}`),
    ...(opts.tx ? [opts.tx] : []),
  );
  return { rpc, provider, owner, executor, feeder, outsider, deploy, token, wrapped, extra, router, fund, args, feed, swap, deadline };
}

test('feeding transfers real balances and emits exact public text/hash; counters credit only the sender', async t => {
  const f = await fixture(t);
  const receipt = await sent(f.feed());
  const event = receipt.logs.map(log => { try { return f.fund.interface.parseLog(log); } catch { return null; } }).find(log => log?.name === 'Fed');
  assert.equal(event.args.feedId, 1n);
  assert.equal(event.args.feeder, f.feeder.address);
  assert.equal(event.args.message, 'BNB 牛逼');
  assert.equal(event.args.messageHash, keccak256(toUtf8Bytes('BNB 牛逼')));
  assert.equal(event.args.requestedAmount, 10_000n);
  assert.equal(event.args.receivedAmount, 10_000n);
  await sent(f.feed(20n));
  assert.equal(await f.fund.totalFeeds(), 2n);
  assert.equal(await f.fund.totalFed(), 10_020n);
  assert.equal(await f.fund.fedBy(f.feeder.address), 10_020n);
  assert.equal(await f.fund.fedBy(f.owner.address), 0n);
  assert.equal(await f.token.balanceOf(f.fund.target), 10_020n);
});

test('transfer-tax FLY records net receipts; user minimum protects against an unexpected tax', async t => {
  const f = await fixture(t);
  await sent(f.token.setTax(1000));
  await sent(f.feed(1000n, 900n));
  assert.equal(await f.fund.totalFed(), 900n);
  await assert.rejects(f.fund.connect(f.feeder).feed.staticCall(1000n, 901n, 'tax'), /BelowMinimumReceived/);
  assert.equal(await f.fund.totalFeeds(), 1n);
});

test('zero net receipts, over-crediting reflection tokens, and false-return tokens are rejected', async t => {
  const f = await fixture(t);
  await sent(f.token.setTax(10_000));
  await assert.rejects(f.fund.connect(f.feeder).feed.staticCall(100n, 1n, 'burn'), /InvalidReceivedAmount/);
  await sent(f.token.setTax(0));
  await sent(f.token.setBonus(true));
  await assert.rejects(f.fund.connect(f.feeder).feed.staticCall(100n, 1n, 'bonus'), /InvalidReceivedAmount/);
  await sent(f.token.setFalseReturn(true));
  await assert.rejects(f.fund.connect(f.feeder).feed.staticCall(100n, 1n, 'false'), /SafeERC20FailedOperation/);
});

test('legacy no-return token is accepted', async t => {
  const f = await fixture(t, { tokenType: 'NoReturnToken' });
  await sent(f.feed(777n));
  assert.equal(await f.fund.totalFed(), 777n);
});

test('amount, allowance, sender and UTF-8 byte limits are enforced', async t => {
  const f = await fixture(t);
  for (const [amount, minimum] of [[0n, 0n], [1n, 0n], [1n, 2n]]) {
    await assert.rejects(f.fund.connect(f.feeder).feed.staticCall(amount, minimum, 'x'), /InvalidAmount/);
  }
  for (const message of ['', 'x'.repeat(1025), '蝇'.repeat(342)]) {
    await assert.rejects(f.fund.connect(f.feeder).feed.staticCall(1n, 1n, message), /InvalidMessageLength/);
  }
  await sent(f.feed(1n, 1n, 'x'.repeat(1024)));
  await sent(f.feed(1n, 1n, '蝇'.repeat(341)));
  await assert.rejects(f.fund.connect(f.outsider).feed.staticCall(1n, 1n, 'impersonate'), error => {
    // The vault ABI does not include every custom error emitted by an external token.
    assert.equal(f.token.interface.parseError(error.data).name, 'ERC20InsufficientAllowance');
    return true;
  });
});

test('direct token donations change the balance but never create feeding credit', async t => {
  const f = await fixture(t);
  await sent(f.token.connect(f.feeder).transfer(f.fund.target, 123n));
  assert.equal(await f.token.balanceOf(f.fund.target), 123n);
  assert.equal(await f.fund.totalFed(), 0n);
  assert.equal(await f.fund.totalFeeds(), 0n);
});

test('token callback cannot reenter feed; successful outer receipt is counted exactly once', async t => {
  const f = await fixture(t);
  await sent(f.token.setCallback(f.fund.target, f.fund.interface.encodeFunctionData('feed', [1n, 1n, 'nested'])));
  await sent(f.feed());
  assert.equal(await f.token.callbackSucceeded(), false);
  assert.equal((await f.token.callbackResult()).slice(0, 10), id('ReentrancyGuardReentrantCall()').slice(0, 10));
  assert.equal(await f.fund.totalFeeds(), 1n);
});

test('BNB donations can be wrapped by anyone but WBNB remains in the fund and creates no feeding credit', async t => {
  const f = await fixture(t);
  await sent(f.feeder.sendTransaction({ to: f.fund.target, value: 123n }));
  await sent(f.fund.connect(f.outsider).wrapBNB());
  assert.equal(await f.wrapped.balanceOf(f.fund.target), 123n);
  assert.equal(await f.wrapped.balanceOf(f.outsider.address), 0n);
  assert.equal(BigInt(await f.rpc.request({ method: 'eth_getBalance', params: [f.fund.target, 'latest'] })), 0n);
  assert.equal(await f.fund.totalFed(), 0n);
  await assert.rejects(f.fund.wrapBNB.staticCall(), /InvalidAmount/);
});

test('executor swaps both directions and via WBNB; all outputs remain in the vault and allowances reset', async t => {
  const f = await fixture(t);
  await sent(f.feed());
  await sent(f.swap());
  assert.equal(await f.router.lastRecipient(), f.fund.target);
  assert.equal(await f.token.balanceOf(f.fund.target), 9000n);
  assert.equal(await f.wrapped.balanceOf(f.fund.target), 1000n);
  assert.equal(await f.wrapped.balanceOf(f.executor.address), 0n);
  assert.equal(await f.token.allowance(f.fund.target, f.router.target), 0n);
  await sent(f.swap(100n, 100n, { path: [f.wrapped.target, f.token.target] }));
  await sent(f.swap(500n, 500n, { path: [f.token.target, f.wrapped.target, f.extra.target] }));
  assert.equal(await f.extra.balanceOf(f.fund.target), 500n);
  assert.equal(await f.fund.totalFed(), 10_000n); // historical inflows, not current NAV
});

test('input and output transfer taxes are measured at the actual receiving address', async t => {
  const f = await fixture(t);
  await sent(f.feed());
  await sent(f.token.setTax(1000));
  await sent(f.extra.setTax(1000));
  const receipt = await sent(f.swap(1000n, 810n, { path: [f.token.target, f.extra.target] }));
  const event = receipt.logs.map(log => { try { return f.fund.interface.parseLog(log); } catch { return null; } }).find(log => log?.name === 'Swapped');
  assert.equal(event.args.amountIn, 1000n);
  assert.equal(event.args.amountOut, 810n);
  assert.equal(await f.extra.balanceOf(f.fund.target), 810n);
});

test('deployer and outsiders have no executor privileges', async t => {
  const f = await fixture(t);
  await sent(f.feed());
  for (const signer of [f.owner, f.outsider, f.feeder]) {
    await assert.rejects(f.fund.connect(signer).swapExactTokens.staticCall(1n, 1n, [f.token.target, f.wrapped.target], await f.deadline(), id('bad')), /NotExecutor/);
  }
});

test('unknown, repeated, empty and overly long routes are rejected', async t => {
  const f = await fixture(t);
  const other = await f.deploy('MockToken');
  await sent(f.feed());
  for (const path of [[], [f.token.target], [f.token.target, f.token.target], [f.token.target, f.wrapped.target, f.token.target], [f.token.target, f.wrapped.target, f.extra.target, f.token.target]]) {
    await assert.rejects(f.fund.connect(f.executor).swapExactTokens.staticCall(1n, 1n, path, await f.deadline(), id('path')), /InvalidPath/);
  }
  await assert.rejects(f.fund.connect(f.executor).swapExactTokens.staticCall(1n, 1n, [f.token.target, other.target], await f.deadline(), id('unknown')), /AssetNotAllowed/);
});

test('zero amount/minimum, absent decision hash, expired and excessively long deadlines are rejected', async t => {
  const f = await fixture(t);
  await sent(f.feed());
  const call = (amount, min, expiry, hash) => f.fund.connect(f.executor).swapExactTokens.staticCall(amount, min, [f.token.target, f.wrapped.target], expiry, hash);
  await assert.rejects(call(0n, 1n, await f.deadline(), id('d')), /InvalidAmount/);
  await assert.rejects(call(1n, 0n, await f.deadline(), id('d')), /InvalidAmount/);
  await assert.rejects(call(1n, 1n, await f.deadline(), ZeroHash), /MissingDecisionHash/);
  await assert.rejects(call(1n, 1n, await f.deadline(-1), id('d')), /InvalidDeadline/);
  await assert.rejects(call(1n, 1n, await f.deadline(601), id('d')), /InvalidDeadline/);
});

test('successful decision hashes cannot execute twice', async t => {
  const f = await fixture(t);
  await sent(f.feed());
  await sent(f.swap(100n, 100n, { hash: id('one-decision') }));
  assert.equal(await f.fund.executedDecision(id('one-decision')), true);
  await assert.rejects(f.fund.connect(f.executor).swapExactTokens.staticCall(100n, 100n, [f.token.target, f.wrapped.target], await f.deadline(), id('one-decision')), /DecisionAlreadyExecuted/);
});

test('per-trade cap and cumulative daily cap stop splitting a large sell into many transactions', async t => {
  const f = await fixture(t);
  await sent(f.feed());
  await assert.rejects(f.fund.connect(f.executor).swapExactTokens.staticCall(1001n, 1n, [f.token.target, f.wrapped.target], await f.deadline(), id('too-big')), /TradeLimitExceeded/);
  for (const amount of [1000n, 900n, 600n]) await sent(f.swap(amount, amount));
  assert.equal((await f.fund.dailyBudget(f.token.target)).spent, 2500n);
  await assert.rejects(f.fund.connect(f.executor).swapExactTokens.staticCall(1n, 1n, [f.token.target, f.wrapped.target], await f.deadline(), id('split')), /DailyLimitExceeded/);
  await sent(f.feed()); // New deposits cannot reset/increase the opening-balance daily budget.
  await assert.rejects(f.fund.connect(f.executor).swapExactTokens.staticCall(1n, 1n, [f.token.target, f.wrapped.target], await f.deadline(), id('deposit-reset')), /DailyLimitExceeded/);
  await f.rpc.request({ method: 'evm_increaseTime', params: [86400] });
  await f.rpc.request({ method: 'evm_mine', params: [] });
  await sent(f.swap(1000n, 1000n));
  assert.equal((await f.fund.dailyBudget(f.token.target)).openingBalance, 17_500n);
  assert.equal((await f.fund.dailyBudget(f.token.target)).spent, 1000n);
});

test('low output reverts an actual mined transaction and restores funds, allowance, budget and decision ID', async t => {
  const f = await fixture(t);
  await sent(f.feed());
  await sent(f.router.setOutputBps(5000));
  await assert.rejects(sent(f.swap(1000n, 999n, { hash: id('rollback'), tx: { gasLimit: 1_000_000 } })));
  assert.equal(await f.token.balanceOf(f.fund.target), 10_000n);
  assert.equal(await f.wrapped.balanceOf(f.fund.target), 0n);
  assert.equal(await f.token.allowance(f.fund.target, f.router.target), 0n);
  assert.equal((await f.fund.dailyBudget(f.token.target)).spent, 0n);
  assert.equal(await f.fund.executedDecision(id('rollback')), false);
  await sent(f.router.setOutputBps(10_000));
  await sent(f.swap(1000n, 1000n, { hash: id('rollback') }));
});

test('router diversion fails atomically because output must reach the fund', async t => {
  const f = await fixture(t);
  await sent(f.feed());
  await sent(f.router.setDivertTo(f.outsider.address));
  await assert.rejects(sent(f.swap(1000n, 1n, { tx: { gasLimit: 1_000_000 } })));
  assert.equal(await f.token.balanceOf(f.fund.target), 10_000n);
  assert.equal(await f.wrapped.balanceOf(f.outsider.address), 0n);
});

test('unexpected input consumption is rejected even when the router sends sufficient output', async t => {
  const f = await fixture(t);
  await sent(f.feed());
  await sent(f.router.setSkipInput(true));
  await assert.rejects(f.fund.connect(f.executor).swapExactTokens.staticCall(100n, 100n, [f.token.target, f.wrapped.target], await f.deadline(), id('no-input')), /UnexpectedInputSpent/);
});

test('router callbacks cannot reenter vault operations', async t => {
  const f = await fixture(t);
  await sent(f.feed());
  await sent(f.router.setCallback(f.fund.target, f.fund.interface.encodeFunctionData('wrapBNB')));
  await sent(f.swap());
  assert.equal(await f.router.callbackSucceeded(), false);
  assert.equal((await f.router.callbackResult()).slice(0, 10), id('ReentrancyGuardReentrantCall()').slice(0, 10));
});

test('no withdrawal, approval, upgrade, arbitrary-call or ownership entrypoints exist', async t => {
  const f = await fixture(t);
  const methods = artifact('FlyFund').abi.filter(item => item.type === 'function').map(item => item.name);
  for (const forbidden of ['withdraw', 'transfer', 'approve', 'rescue', 'execute', 'upgradeTo', 'setRouter', 'setExecutor', 'setAllowedAsset', 'owner', 'transferOwnership']) {
    assert.equal(methods.includes(forbidden), false, forbidden);
  }
  await assert.rejects(sent(f.owner.sendTransaction({ to: f.fund.target, data: '0xdeadbeef', gasLimit: 100_000 })));
});

test('constructor rejects missing contracts, invalid limits, duplicate tokens and zero executor', async t => {
  const f = await fixture(t);
  const variants = [
    [0, ZeroAddress], [1, ZeroAddress], [2, f.outsider.address], [3, f.outsider.address],
    [3, f.token.target], [4, [f.token.target]], [4, [f.extra.target, f.extra.target]],
    [4, [f.outsider.address]], [5, 0], [5, 10_001], [6, 999], [6, 10_001],
  ];
  for (const [index, value] of variants) {
    const args = [...f.args]; args[index] = value;
    await assert.rejects(f.deploy('FlyFund', args));
  }
});
