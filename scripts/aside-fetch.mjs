// Wrapper-safe argv: the lock wrapper must not mistake its own argv for a running REPL.
import {spawnSync} from 'node:child_process';
import {asideCmd, SPAWN_SHELL} from '../lib/platform.mjs';
const [exe, account, ...args] = process.argv.slice(2);
const result = spawnSync(asideCmd({...process.env, ALIBABA_SELLER_ASIDE:exe}),
  ['--host','local','--account',account,'repl',...args], {stdio:'inherit',shell:SPAWN_SHELL});
process.exitCode = result.status ?? 1;
