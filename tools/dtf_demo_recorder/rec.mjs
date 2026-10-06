import { chromium } from '/Users/ankit/Projects/cc1/node_modules/playwright/index.mjs';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

const R = path => (process.env.REEL_DIR || '/tmp/dtf-reel') + '/' + path;
const BASE = 'http://127.0.0.1:48731';
const F = name => R('files/' + name);
const FPS = 30;
const ease = k => (k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2);
const CSS = `
.reel-hl{outline:4px solid #FFB800 !important;outline-offset:3px !important;box-shadow:0 0 0 9px rgba(255,184,0,.25) !important;border-radius:10px}
tr.reel-hl{outline:none !important;box-shadow:none !important}
tr.reel-hl > td, tr.reel-hl > th{background:#FFE8A3 !important;box-shadow:inset 0 3px 0 #FFB800, inset 0 -3px 0 #FFB800 !important}
#test-banner{display:none !important}
.reel-tap{position:fixed;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;background:rgba(30,30,30,.20);border:2px solid rgba(255,255,255,.95);box-shadow:0 0 0 2px rgba(0,0,0,.25);pointer-events:none;z-index:2147483647}
`;

const b = await chromium.launch();

async function clip(name, steps) {
    const dir = R('frames/' + name);
    fs.rmSync(dir, { recursive: true, force: true });
    fs.mkdirSync(dir, { recursive: true });
    const ctx = await b.newContext({ viewport: { width: 360, height: 640 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true, serviceWorkers: 'block', bypassCSP: true });
    const p = await ctx.newPage();
    await p.route('**/api/file-check', route => route.fulfill({ status: 204, body: '' }));
    const list = [];
    let n = 0;
    const shot = async () => {
        const f = `${dir}/f${String(n++).padStart(5, '0')}.png`;
        await p.screenshot({ path: f });
        return f;
    };
    const A = {
        p,
        async go(path) {
            await p.goto(BASE + path);
            await p.waitForLoadState('networkidle');
            await p.addStyleTag({ content: CSS });
            await p.evaluate(() => document.fonts.ready);
            await p.waitForTimeout(500);
        },
        async hold(sec) { list.push([await shot(), sec]); },
        async live(ms) {
            const t0 = Date.now();
            let prev = await shot(), last = Date.now();
            while (Date.now() - t0 < ms) {
                const f = await shot();
                const now = Date.now();
                list.push([prev, (now - last) / 1000]);
                prev = f;
                last = now;
            }
            list.push([prev, 0.05]);
        },
        async until(test, maxMs = 15000) {
            const t0 = Date.now();
            let prev = await shot(), last = Date.now();
            while (Date.now() - t0 < maxMs) {
                const done = await p.evaluate(test).catch(() => false);
                const f = await shot();
                const now = Date.now();
                list.push([prev, (now - last) / 1000]);
                prev = f;
                last = now;
                if (done) break;
            }
            list.push([prev, 0.05]);
        },
        async scroll(to, secs = 1) {
            const from = await p.evaluate(() => window.scrollY);
            const max = await p.evaluate(() => document.documentElement.scrollHeight - innerHeight);
            const target = Math.max(0, Math.min(max, to));
            if (Math.abs(target - from) < 2) return;
            const frames = Math.max(2, Math.round(secs * FPS));
            for (let i = 1; i <= frames; i++) {
                await p.evaluate(y => window.scrollTo(0, y), from + (target - from) * ease(i / frames));
                list.push([await shot(), 1 / FPS]);
            }
        },
        async scrollEl(sel, top = 70, secs = 1) {
            const y = await p.evaluate(([s, t]) => {
                const e = document.querySelector(s);
                return e ? e.getBoundingClientRect().top + window.scrollY - t : null;
            }, [sel, top]);
            if (y === null) throw new Error('no element for scroll: ' + sel);
            await A.scroll(y, secs);
        },
        async tap(sel, act) {
            const box = await p.locator(sel).first().boundingBox();
            if (!box) throw new Error('no element to tap: ' + sel);
            await p.evaluate(([x, y]) => {
                const d = document.createElement('div');
                d.className = 'reel-tap';
                d.style.left = x + 'px';
                d.style.top = y + 'px';
                document.body.appendChild(d);
            }, [box.x + box.width / 2, box.y + box.height / 2]);
            list.push([await shot(), 0.32]);
            await p.evaluate(() => document.querySelectorAll('.reel-tap').forEach(e => e.remove()));
            if (act) await act();
            else await p.locator(sel).first().click();
        },
        async hl(sel, on = true) {
            await p.evaluate(([s, o]) => {
                document.querySelectorAll('.reel-hl').forEach(e => e.classList.remove('reel-hl'));
                if (o) document.querySelectorAll(s).forEach(e => e.classList.add('reel-hl'));
            }, [sel, on]);
        },
        async pick(file) {
            await A.tap('#pickBtn, .rows + .picker button, #picker button', () => p.setInputFiles('#fileInput', F(file)));
        },
        async uploaded() {
            await A.until(() => {
                const rows = [...document.querySelectorAll('#rows [data-status]')];
                return rows.length && rows.every(r => r.getAttribute('data-status') === 'accepted' || r.getAttribute('data-status') === 'rejected');
            });
            await p.waitForTimeout(300);
        },
        async zoom(sel, scale = 2, holdSec = 2, inSec = 0.55, outSec = 0.45) {
            const box = await p.locator(sel).first().boundingBox();
            if (!box) throw new Error('no element to zoom: ' + sel);
            const f = await shot();
            const K = 3, FW = 1080, FH = 1920;
            const cw = FW / scale, ch = FH / scale;
            const cx = (box.x + box.width / 2) * K, cy = (box.y + box.height / 2) * K;
            const x1 = Math.max(0, Math.min(FW - cw, cx - cw / 2)), y1 = Math.max(0, Math.min(FH - ch, cy - ch / 2));
            const at = k => { const e = ease(k); return [x1 * e, y1 * e, FW + (cw - FW) * e, FH + (ch - FH) * e].map(v => Math.round(v * 100) / 100); };
            const nIn = Math.max(2, Math.round(inSec * FPS)), nOut = Math.max(2, Math.round(outSec * FPS));
            for (let i = 1; i <= nIn; i++) list.push([f, 1 / FPS, at(i / nIn)]);
            list.push([f, holdSec, at(1)]);
            for (let i = nOut - 1; i >= 0; i--) list.push([f, 1 / FPS, at(i / nOut)]);
        },
        async hlText(text, closest = '') {
            const ok = await p.evaluate(([t, c]) => {
                document.querySelectorAll('.reel-hl').forEach(e => e.classList.remove('reel-hl'));
                let best = null;
                for (const e of document.querySelectorAll('main *, body > *')) {
                    if (!e.textContent.includes(t) || e.children.length > 12) continue;
                    if (!best || e.textContent.length < best.textContent.length) best = e;
                }
                if (!best) return false;
                (c ? best.closest(c) || best : best).classList.add('reel-hl');
                return true;
            }, [text, closest]);
            if (!ok) throw new Error('no element with text: ' + text);
        },
        async scrollText(text, top = 70, secs = 1) {
            const y = await p.evaluate(([t, tp]) => {
                let best = null;
                for (const e of document.querySelectorAll('main *, body > *')) {
                    if (!e.textContent.includes(t)) continue;
                    if (!best || e.textContent.length < best.textContent.length) best = e;
                }
                return best ? best.getBoundingClientRect().top + window.scrollY - tp : null;
            }, [text, top]);
            if (y === null) throw new Error('no element with text: ' + text);
            await A.scroll(y, secs);
        },
        async quiet(test, maxMs = 8000) {
            const t0 = Date.now();
            while (Date.now() - t0 < maxMs && !(await p.evaluate(test).catch(() => false))) await p.waitForTimeout(100);
        },
        async removeAll() {
            const btn = p.locator('#rows .btn-x').first();
            await A.tap('#rows .btn-x', () => btn.click());
            await A.until(() => document.querySelectorAll('#rows [data-status]').length === 0, 5000);
        },
    };
    await steps(A);
    await ctx.close();
    const total = list.reduce((s, [, d]) => s + d, 0);
    fs.writeFileSync(`${dir}/manifest.json`, JSON.stringify(list.map(([f, d, c]) => ({ f, d, c: c || null }))));
    fs.mkdirSync(R('clips'), { recursive: true });
    execFileSync('python3', [new URL('./encode.py', import.meta.url).pathname, `${dir}/manifest.json`, R(`clips/${name}.mp4`)], { stdio: 'inherit' });
    console.log(name, 'frames', n, 'secs', total.toFixed(2));
}

const which = process.argv.slice(2);
const popOpen = () => document.getElementById('pop').open;
const priceSettled = () => { const e = document.querySelector('#rows .lprice'); return !!e && !e.classList.contains('busy') && e.textContent.trim().length > 0; };
const typeCopies = (A, v) => async () => { const i = A.p.locator('#rows .copies-in'); await i.fill(v); await i.dispatchEvent('change'); await i.blur(); };
const packReady = () => { const b = document.getElementById('packBox'); const s = document.getElementById('summary'); return !!b && !b.hidden && !!b.querySelector('canvas') && !!s && !s.classList.contains('busy'); };
const designCopies = (A, sel, v) => async () => { const i = A.p.locator(sel); await i.fill(v); await i.dispatchEvent('change'); await i.blur(); };
const CLIPS = {
    async sharp(A) {
        await A.go('/'); await A.hold(1.0);
        await A.tap('#designBtn', () => A.p.setInputFiles('#designInput', F('groom-squad.png')));
        await A.uploaded(); await A.until(packReady, 12000); await A.hold(0.5);
        await A.scrollEl('#rows', 64, 0.6);
        await A.hl('#rows .row.is-design .fsize'); await A.hold(2.4);
        await A.hl('#rows .row.is-design .dnote'); await A.hold(3.0); await A.hl('', false);
        const plus = '#rows .dwidth button[aria-label^="Bigger"]';
        for (let i = 0; i < 4 && (await A.p.locator(plus).isEnabled()); i++) { await A.tap(plus); await A.live(260); }
        await A.hl('#rows .row.is-design .dnote'); await A.hold(2.4); await A.hl('', false);
        const width = '#rows .width-in';
        await A.tap(width, designCopies(A, width, '12'));
        await A.until(() => /Bigger would not print sharp/.test(document.querySelector('#rows .dnote').textContent), 6000);
        await A.hl('#rows .row.is-design .dnote'); await A.hold(3.8); await A.hl('', false);
        await A.hl('#rows .dwidth'); await A.hold(2.0); await A.hl('', false);
        await A.until(packReady, 12000);
        await A.scrollEl('#packBox', 64, 0.9);
        await A.hl('#packBox'); await A.hold(3.4); await A.hl('', false); await A.hold(0.5);
    },
    async stickers(A) {
        await A.go('/'); await A.hold(1.2);
        await A.hl('#designPicker button, #designHint'); await A.hold(2.8); await A.hl('', false);
        await A.tap('#designBtn', () => A.p.setInputFiles('#designInput', F('bride-tribe.png')));
        await A.uploaded(); await A.until(packReady, 12000); await A.hold(0.5);
        await A.scrollEl('#rows', 64, 0.6);
        await A.hl('#rows .row.is-design .fsize'); await A.hold(2.4);
        await A.hl('#rows .row.is-design .dnote'); await A.hold(2.8); await A.hl('', false);
        const first = '#rows li:nth-of-type(1) .copies-in:not(.width-in)';
        await A.tap(first, designCopies(A, first, '10')); await A.until(packReady, 12000); await A.hold(0.6);
        await A.scrollEl('#packBox', 64, 0.9);
        await A.hl('#packBox'); await A.hold(3.8); await A.hl('', false);
        await A.tap('#designBtn', () => A.p.setInputFiles('#designInput', F('gym-badge.png')));
        await A.uploaded(); await A.until(packReady, 12000); await A.hold(0.8);
        const second = '#rows li:nth-of-type(2) .copies-in:not(.width-in)';
        await A.tap(second, designCopies(A, second, '6')); await A.until(packReady, 12000); await A.hold(0.6);
        await A.scrollEl('#packBox', 64, 0.9);
        await A.hl('#packBox'); await A.hold(4.2); await A.hl('', false); await A.hold(0.5);
    },
    async transparent(A) {
        await A.go('/'); await A.hold(1.3);
        await A.pick('white-bg.png'); await A.uploaded(); await A.hold(0.4);
        await A.zoom('#rows .thumb', 2.2, 1.8);
        await A.tap('.chip-note'); await A.live(350);
        await A.scrollEl('#rows', 64, 0.7);
        await A.hl('#rows .notes .msg'); await A.hold(4.8); await A.hl('', false);
        await A.removeAll(); await A.scroll(0, 0.5);
        await A.pick('glow.png'); await A.uploaded(); await A.hold(0.4);
        await A.tap('.chip-note'); await A.live(350);
        await A.scrollEl('#rows', 64, 0.7);
        await A.hl('#rows .notes .msg'); await A.hold(5.2); await A.hl('', false);
        await A.removeAll(); await A.scroll(0, 0.5);
        await A.pick('transparent.png'); await A.uploaded(); await A.hold(0.4);
        await A.zoom('#rows .thumb', 2.2, 1.8);
        await A.hl('#rows [data-status="accepted"]'); await A.hold(2.4); await A.hl('', false);
        await A.tap('#moreToggle'); await A.live(350);
        await A.scrollEl('.tips', 120, 0.9);
        await A.hl('.tips li:nth-child(1)'); await A.hold(2.8);
        await A.hl('.tips li:nth-child(3)'); await A.hold(2.8); await A.hl('', false); await A.hold(0.4);
    },
    async resolution(A) {
        await A.go('/'); await A.hold(1.3);
        await A.tap('#pickBtn', () => A.p.setInputFiles('#fileInput', F('300-dpi.png')));
        await A.until(popOpen, 8000); await A.live(300);
        await A.hl('#popBody p:nth-child(1)'); await A.hold(3.4);
        await A.hl('#popBody p:nth-child(2)'); await A.hold(3.4);
        await A.hl('#popBody p:nth-child(3)'); await A.hold(4.6); await A.hl('', false);
        await A.tap('#popClose'); await A.live(300);
        await A.tap('#moreToggle'); await A.live(350);
        await A.hl('#moreDpi'); await A.hold(4.6); await A.hl('', false);
        await A.tap('#moreToggle'); await A.live(300);
        await A.pick('6840px.png'); await A.uploaded(); await A.hold(0.4);
        await A.hl('#rows [data-status="accepted"]'); await A.hold(4.2); await A.hl('', false); await A.hold(0.4);
    },
    async canva(A) {
        await A.go('/'); await A.hold(1.3);
        await A.tap('#pickBtn', () => A.p.setInputFiles('#fileInput', F('canva-1x.png')));
        await A.until(popOpen, 8000); await A.live(300);
        await A.hl('#popBody p:nth-child(1)'); await A.hold(3.6);
        await A.hl('#popBody p:nth-child(2)'); await A.hold(3.0);
        await A.hl('#popBody p:nth-child(4)'); await A.hold(6.0); await A.hl('', false);
        await A.tap('#popClose'); await A.live(300);
        await A.pick('canva-3.125x.png'); await A.uploaded(); await A.hold(0.4);
        await A.hl('#rows [data-status="accepted"]'); await A.hold(4.4); await A.hl('', false);
        await A.tap('#moreToggle'); await A.live(350);
        await A.hl('#moreDpi'); await A.hold(4.2); await A.hl('', false); await A.hold(0.4);
    },
    async pieces(A) {
        await A.go('/'); await A.hold(1.3);
        await A.pick('28-designs.png'); await A.uploaded(); await A.hold(0.4);
        await A.zoom('#rows .thumb', 2.2, 2.4);
        await A.hl('#rows .rctl'); await A.hold(2.6);
        await A.tap('#rows .stepper .st-btn:last-child'); await A.until(priceSettled, 8000); await A.hold(1.2);
        await A.tap('#rows .stepper .st-btn:last-child'); await A.until(priceSettled, 8000); await A.hold(1.2);
        await A.tap('#rows .copies-in', typeCopies(A, '28')); await A.until(priceSettled, 8000); await A.hold(3.8);
        await A.tap('#rows .copies-in', typeCopies(A, '1')); await A.quiet(priceSettled); await A.hold(2.0); await A.hl('', false);
        await A.go('/about'); await A.hold(0.8);
        await A.scrollText('You pay for the real height of your sheet', 230, 1.0);
        await A.hlText('You pay for the real height of your sheet'); await A.hold(4.6); await A.hl('', false); await A.hold(0.4);
    },
    async gang(A) {
        await A.go('/'); await A.hold(1.0);
        await A.hl('#introSize'); await A.hold(3.0); await A.hl('', false);
        await A.pick('28-designs.png'); await A.uploaded(); await A.hold(0.4);
        await A.zoom('#rows .thumb', 2.2, 3.0);
        await A.hl('#rows .fsize'); await A.hold(2.4);
        await A.hl('#rows .rctl'); await A.hold(3.0); await A.hl('', false);
        await A.scroll(0, 0.5);
        await A.tap('#moreToggle'); await A.live(350);
        await A.hl('#moreDpi'); await A.hold(3.4);
        await A.hl('#moreExample'); await A.hold(3.4); await A.hl('', false);
        await A.go('/about'); await A.hold(0.8);
        await A.scrollText('Width 57.9 cm', 200, 1.0);
        await A.hlText('Width 57.9 cm'); await A.hold(4.2); await A.hl('', false); await A.hold(0.4);
    },
    async press(A) {
        await A.go('/press'); await A.hold(1.8);
        await A.scrollText('Temperature and time', 64, 1.0);
        await A.hlText('Thin T-shirts (cotton, polyester)', 'tr'); await A.hold(4.0);
        await A.hlText('Hoodies and sweatshirts', 'tr'); await A.hold(4.0);
        await A.hlText('Pressure: medium-firm'); await A.hold(3.0); await A.hl('', false);
        await A.scrollText('Step by step', 64, 1.0);
        await A.hlText('Heat the press to the temperature'); await A.hold(2.2);
        await A.hlText('Lay the print on the garment'); await A.hold(2.2);
        await A.hlText('Press for 5 seconds with medium-firm'); await A.hold(2.4);
        await A.hlText('Let it cool fully'); await A.hold(3.2);
        await A.hlText('Put a Teflon sheet or baking paper'); await A.hold(3.4); await A.hl('', false);
        await A.scrollText('Test first', 90, 0.8);
        await A.hlText('Always test on a spare garment first'); await A.hold(3.0); await A.hl('', false); await A.hold(0.4);
    },
    async launch(A) {
        await A.go('/'); await A.hold(1.6);
        await A.hl('#intro'); await A.hold(2.6); await A.hl('', false);
        await A.pick('28-designs.png'); await A.uploaded(); await A.hold(0.4);
        await A.hl('#rows [data-status="accepted"]'); await A.hold(3.0); await A.hl('', false);
        await A.scrollEl('#delCard', 64, 1.2); await A.hold(3.2);
        await A.go('/about'); await A.hold(0.8);
        await A.hlText('We print DTF heat-transfer sheets from your own PNG file'); await A.hold(4.4);
        await A.hlText('Printed by our partner in Surat'); await A.hold(3.4); await A.hl('', false);
        await A.go('/press'); await A.hold(1.2);
        await A.scrollText('Temperature and time', 64, 1.0);
        await A.hlText('Thin T-shirts (cotton, polyester)', 'tr'); await A.hold(2.2);
        await A.hlText('Hoodies and sweatshirts', 'tr'); await A.hold(2.2); await A.hl('', false); await A.hold(0.4);
    },
};

for (const name of which) {
    if (!CLIPS[name]) throw new Error('unknown clip ' + name);
    await clip(name, CLIPS[name]);
}
await b.close();
