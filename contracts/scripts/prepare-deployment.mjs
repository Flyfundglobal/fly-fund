// Prepares an UNSIGNED deployment. No private keys, signer, sendTransaction or broadcast code.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { AbiCoder, Contract, ContractFactory, JsonRpcProvider, ZeroAddress, getAddress, keccak256 } from 'ethers';
import { compile, root } from './compile.mjs';

export function validateConfig(config) {
  if (![56, 97].includes(config.chainId)) throw new Error('chainId must explicitly be BSC 56 or BSC testnet 97');
  const normalized = { ...config };
  for (const key of ['flyToken', 'executor', 'router', 'wrappedNative']) {
    if (!config[key]) throw new Error(`Missing ${key}`);
    normalized[key] = getAddress(config[key]);
    if (normalized[key] === ZeroAddress) throw new Error(`${key} cannot be zero`);
  }
  for (const key of ['maxTradeBps', 'dailySpendBps']) {
    if (!Number.isInteger(config[key]) || config[key] < 1 || config[key] > 10_000) {
      throw new Error(`Choose ${key} explicitly, integer 1..10000`);
    }
  }
  if (config.dailySpendBps < config.maxTradeBps) throw new Error('dailySpendBps must be >= maxTradeBps');
  if (!Array.isArray(config.extraAssets) || config.extraAssets.length > 14) throw new Error('extraAssets must be an array with at most 14 entries');
  normalized.extraAssets = config.extraAssets.map(getAddress);
  const assets = [normalized.flyToken, normalized.wrappedNative, ...normalized.extraAssets];
  if (assets.includes(ZeroAddress) || new Set(assets).size !== assets.length) throw new Error('Asset addresses must be distinct and nonzero');
  return normalized;
}

export async function prepareDeployment(rawConfig, provider) {
  const config = validateConfig(rawConfig);
  const network = await provider.getNetwork();
  if (Number(network.chainId) !== config.chainId) throw new Error('RPC chainId does not match configuration');
  for (const address of [config.flyToken, config.router, config.wrappedNative, ...config.extraAssets]) {
    if (await provider.getCode(address) === '0x') throw new Error(`No deployed contract at ${address}`);
  }
  const router = new Contract(config.router, ['function WETH() view returns (address)', 'function factory() view returns (address)'], provider);
  if (getAddress(await router.WETH()) !== config.wrappedNative) throw new Error('Router WETH() differs from configured wrappedNative');
  const factoryAddress = getAddress(await router.factory());
  if (await provider.getCode(factoryAddress) === '0x') throw new Error('Router factory has no code');
  const assets = [];
  for (const address of [config.flyToken, config.wrappedNative, ...config.extraAssets]) {
    const token = new Contract(address, ['function decimals() view returns (uint8)', 'function symbol() view returns (string)'], provider);
    const decimals = Number(await token.decimals());
    const symbol = await token.symbol();
    assets.push({ address, symbol, decimals });
  }
  const { output, input, compiler } = compile();
  const artifact = output.contracts['src/FlyFund.sol'].FlyFund;
  const args = [config.flyToken, config.executor, config.router, config.wrappedNative, config.extraAssets, config.maxTradeBps, config.dailySpendBps];
  const factory = new ContractFactory(artifact.abi, artifact.evm.bytecode.object);
  const unsigned = await factory.getDeployTransaction(...args);
  return {
    config, assets, factoryAddress, compiler,
    constructorArguments: AbiCoder.defaultAbiCoder().encode(['address', 'address', 'address', 'address', 'address[]', 'uint256', 'uint256'], args),
    initCodeHash: keccak256(unsigned.data),
    transaction: { chainId: config.chainId, data: unsigned.data, value: '0x0' },
    abi: artifact.abi, standardInput: input,
    status: 'UNSIGNED_NOT_DEPLOYED',
    remainingChecks: ['Verify address provenance and code, including proxy/admin privileges', 'Confirm actual Flap token version and V2 pool/routes', 'Fork-test real transfer taxes, output bounds and receipts', 'Finalize executor custody and loss limits'],
  };
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const configPath = process.argv[2];
  if (!configPath || !process.env.BSC_RPC_URL) throw new Error('Usage: BSC_RPC_URL=... npm run prepare -- path/to/config.json');
  const provider = new JsonRpcProvider(process.env.BSC_RPC_URL);
  try {
    const result = await prepareDeployment(JSON.parse(fs.readFileSync(configPath, 'utf8')), provider);
    fs.mkdirSync(path.join(root, 'artifacts'), { recursive: true });
    const outputPath = path.join(root, 'artifacts/deployment-plan.json');
    fs.writeFileSync(outputPath, JSON.stringify(result, null, 2));
    console.log(`Unsigned deployment prepared: ${outputPath}`);
    console.log('No transaction was signed or submitted. Inspect the plan before deploying.');
  } finally { provider.destroy(); }
}
