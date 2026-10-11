import {execFileSync} from 'node:child_process';
import {tick} from './worker.mjs';

const token = execFileSync('gh', ['auth', 'token', '--user', 'thakyanamtumhara'], {encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe']}).trim();
const now = process.argv[2] ? Date.parse(process.argv[2]) : Date.now();
const result = await tick({ENABLED: 'true', DRY_RUN: 'true', HELD_SHORT_GH_TOKEN: token, PROBE: process.env.PROBE}, {now});
console.log(JSON.stringify({at: new Date(now).toISOString(), ...result}));
