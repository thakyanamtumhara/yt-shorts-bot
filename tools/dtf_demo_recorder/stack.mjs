import { startStack } from '/Users/ankit/Projects/dtf-app/test/e2e/buyer-helpers.mjs';
const s = await startStack({
    logName: 'server-reel.log',
    settings: { price_per_metre_paise: '15500', gst_bp: '1800', delivery_types: '{"courier":true,"bike":true,"pickup":false,"drop":true}', design_check: 'true', designs_on: 'true' },
});
console.log('UP', s.base, 'pid', s.pid);
const stop = async () => { await s.stop(); console.log('STOPPED'); process.exit(0); };
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
setInterval(() => {}, 1 << 30);
