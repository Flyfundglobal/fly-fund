import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import solc from 'solc';

export const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export function compile({ tests = false } = {}) {
  const sources = {};
  for (const folder of ['src', ...(tests ? ['test/mocks'] : [])]) {
    const directory = path.join(root, folder);
    if (!fs.existsSync(directory)) continue;
    for (const name of fs.readdirSync(directory).filter(name => name.endsWith('.sol')).sort()) {
      sources[`${folder}/${name}`] = { content: fs.readFileSync(path.join(directory, name), 'utf8') };
    }
  }
  // Pin an explicit conservative EVM target. Never inherit the compiler's newest default.
  const settings = {
    optimizer: { enabled: true, runs: 200 },
    evmVersion: 'paris',
    outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object', 'evm.deployedBytecode.object'] } },
  };
  const output = JSON.parse(solc.compile(JSON.stringify({ language: 'Solidity', sources, settings }), {
    import(name) {
      if (!name.startsWith('@openzeppelin/contracts/')) return { error: `Import not allowed: ${name}` };
      const filename = path.resolve(root, 'node_modules', name);
      const allowedRoot = path.resolve(root, 'node_modules/@openzeppelin/contracts') + path.sep;
      if (!filename.startsWith(allowedRoot)) return { error: 'Import escaped dependency root' };
      try {
        const content = fs.readFileSync(filename, 'utf8');
        sources[name] = { content };
        return { contents: content };
      } catch { return { error: `Missing dependency: ${name}; run npm ci` }; }
    },
  }));
  for (const error of output.errors ?? []) {
    if (error.severity !== 'error') console.warn(error.formattedMessage);
  }
  const errors = (output.errors ?? []).filter(error => error.severity === 'error');
  if (errors.length) throw new Error(errors.map(error => error.formattedMessage).join('\n'));
  return { output, input: { language: 'Solidity', sources, settings }, compiler: solc.version() };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { output, input, compiler } = compile();
  const directory = path.join(root, 'artifacts');
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(path.join(directory, 'standard-input.json'), JSON.stringify(input, null, 2));
  fs.writeFileSync(path.join(directory, 'compiler.json'), JSON.stringify({ compiler, settings: input.settings }, null, 2));
  for (const [sourceName, contracts] of Object.entries(output.contracts)) {
    if (!sourceName.startsWith('src/')) continue;
    for (const [contractName, contract] of Object.entries(contracts)) {
      fs.writeFileSync(path.join(directory, `${contractName}.json`), JSON.stringify({
        contractName, sourceName, compiler, abi: contract.abi,
        bytecode: `0x${contract.evm.bytecode.object}`,
        deployedBytecode: `0x${contract.evm.deployedBytecode.object}`,
      }, null, 2));
      console.log(`${contractName}: ${contract.evm.deployedBytecode.object.length / 2} deployed bytes`);
    }
  }
}
