import test from 'node:test';
import assert from 'node:assert/strict';
import { validateConfig, prepareDeployment } from '../scripts/prepare-deployment.mjs';

const valid = () => ({ chainId: 97, flyToken: '0x0000000000000000000000000000000000000001', executor: '0x0000000000000000000000000000000000000002', router: '0x0000000000000000000000000000000000000003', wrappedNative: '0x0000000000000000000000000000000000000004', extraAssets: [], maxTradeBps: 1000, dailySpendBps: 2500 });

test('deployment config requires explicit addresses, chain and limits', () => {
  assert.equal(validateConfig(valid()).chainId, 97);
  for (const patch of [{ flyToken: null }, { executor: null }, { router: null }, { chainId: 1 }, { maxTradeBps: null }, { dailySpendBps: 0 }, { maxTradeBps: 10001 }, { dailySpendBps: 999 }, { extraAssets: [valid().flyToken] }]) {
    assert.throws(() => validateConfig({ ...valid(), ...patch }));
  }
});

test('unsigned deployment preparation rejects wrong networks and missing code before proceeding', async () => {
  await assert.rejects(prepareDeployment(valid(), { getNetwork: async () => ({ chainId: 56n }) }), /chainId/);
  await assert.rejects(prepareDeployment(valid(), { getNetwork: async () => ({ chainId: 97n }), getCode: async () => '0x' }), /No deployed contract/);
});
