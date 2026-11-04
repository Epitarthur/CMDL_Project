// Run with: node --test tests/browser/test_feed_visibility.mjs
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {runInNewContext} from 'node:vm';

const source = readFileSync(new URL('../../src/cmdl/ui/feed_visibility.js', import.meta.url), 'utf8');

function browser() {
    let now = 0, interval, observer, focused = true;
    let disconnected = false;
    const targets = {a: {isConnected: true}, b: {isConnected: true}};
    const reports = [];
    const window = {};
    const document = {
        visibilityState: 'visible', hasFocus: () => focused,
        querySelector: key => targets[key],
    };
    const mount = runInNewContext(source.replace('export default function', '(function') + ')', {
        document, window, performance: {now: () => now},
        setInterval: fn => { interval = fn; return 1; },
        clearInterval: () => { interval = undefined; },
        IntersectionObserver: class {
            constructor(callback) { observer = callback; }
            observe() {}
            unobserve() {}
            disconnect() { disconnected = true; }
        },
    });
    return {
        reports, document,
        mount: (feed_id = 'first') => mount({
            data: {feed_id, cards: [{content_id: '1', selector: 'a'}, {content_id: '2', selector: 'b'}]},
            setStateValue: (key, value) => reports.push(value),
        }),
        advance: ms => { for (let n = 0; n < ms; n += 250) { now += 250; interval?.(); } },
        visible: (key, ratio) => observer([{
            target: targets[key], isIntersecting: ratio > 0, intersectionRatio: ratio,
        }]),
        focus: value => { focused = value; },
        oldTimer: () => interval,
        get disconnected() { return disconnected; },
    };
}

test('offscreen and briefly visible cards do not count; half-visible cards count once', () => {
    const b = browser(); b.mount(); b.advance(5000);
    assert.equal(b.reports.length, 0);
    b.visible('a', 0.49); b.advance(2000);
    assert.equal(b.reports.length, 0);
    b.visible('a', 0.5); b.advance(750); b.visible('a', 0); b.advance(1000);
    assert.equal(b.reports.length, 0);
    b.visible('a', 0.75); b.advance(1000);
    assert.deepEqual(Array.from(b.reports[0].content_ids), ['1']);
    b.advance(5000);
    assert.equal(b.reports.length, 1);
});

test('hidden and unfocused time resets continuous visibility', () => {
    const b = browser(); b.mount(); b.visible('a', 1); b.advance(500);
    b.document.visibilityState = 'hidden'; b.advance(1000);
    b.document.visibilityState = 'visible'; b.advance(500);
    assert.equal(b.reports.length, 0);
    b.focus(false); b.advance(1000); b.focus(true); b.advance(750);
    assert.equal(b.reports.length, 0);
    b.advance(250);
    assert.equal(b.reports.length, 1);
});

test('rerenders preserve receipts; multiple visible cards are batched', () => {
    const b = browser(); const cleanup = b.mount();
    b.visible('a', 1); b.visible('b', 1); b.advance(1000);
    assert.deepEqual(Array.from(b.reports[0].content_ids), ['1', '2']);
    cleanup(); assert.equal(b.disconnected, true);
    b.mount(); b.visible('a', 1); b.advance(2000);
    assert.equal(b.reports.length, 1);
});

test('a new feed resets receipts and rejects stale timers', () => {
    const b = browser(); b.mount(); b.visible('a', 1); b.advance(750);
    const oldTick = b.oldTimer();
    b.mount('second'); b.visible('b', 1); b.advance(250); oldTick();
    assert.equal(b.reports.length, 0);
    b.advance(750);
    assert.equal(b.reports.length, 1);
    assert.equal(b.reports[0].feed_id, 'second');
    assert.deepEqual(Array.from(b.reports[0].content_ids), ['2']);
});
