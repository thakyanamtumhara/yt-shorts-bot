import {execFileSync} from 'node:child_process';
import {tick} from './worker.mjs';

const token = execFileSync('gh', ['auth', 'token'], {encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe']}).trim();
const result = await tick({ENABLED: 'true', DRY_RUN: 'true', REVIEWED_IG_GH_TOKEN: token});
console.log(JSON.stringify(result, null, 2));
