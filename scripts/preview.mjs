import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { projectRoot } from "./sites-env.mjs";

// Serve a fixed snapshot so a later build cannot remove live assets mid-request.
const build = path.join(projectRoot, "dist");
if (!existsSync(path.join(build, "server/wrangler.json"))) {
  throw new Error("Run npm run build before starting the preview.");
}
const releases = path.join(projectRoot, ".sites-runtime/preview-releases");
const release = path.join(releases, `${Date.now()}-${process.pid}`);
const pointer = path.join(releases, "current.txt");
mkdirSync(releases, { recursive: true });
cpSync(build, release, { recursive: true });

// A visitor may still have HTML from the previous release during an update.
if (existsSync(pointer)) {
  const previous = path.join(releases, path.basename(readFileSync(pointer, "utf8").trim()));
  const assets = path.join(previous, "client/_next/static");
  if (existsSync(assets)) {
    cpSync(assets, path.join(release, "client/_next/static"), { recursive: true, force: false });
  }
}
writeFileSync(pointer, path.basename(release));
console.log("Starting an isolated build snapshot for the team preview.");
const cli = new URL("../node_modules/wrangler/bin/wrangler.js", import.meta.url);
const child = spawn(process.execPath, [fileURLToPath(cli), "dev", "--config",
  path.join(release, "server/wrangler.json"), "--local", "--persist-to",
  path.join(projectRoot, ".wrangler/state"), "--ip", "127.0.0.1",
  "--inspector-port", "0", ...process.argv.slice(2)], { stdio: "inherit" });
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
child.on("error", error => { console.error(error); process.exitCode = 1; });
child.on("exit", code => { process.exitCode = code ?? 1; });
