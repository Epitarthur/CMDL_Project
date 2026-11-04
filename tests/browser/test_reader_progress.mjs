// Run with: node --test tests/browser/test_reader_progress.mjs
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {runInNewContext} from 'node:vm';

const source = readFileSync(new URL('../../src/cmdl/ui/reader_progress.js', import.meta.url), 'utf8');

function browser() {
    let now = 0, focused = true, bottom = 100, interval, observer, disconnected = false;
    const events = [], listeners = new Map();
    const marker = {isConnected: true, getBoundingClientRect: () => ({bottom})};
    const document = {
        visibilityState: 'visible', hasFocus: () => focused,
        addEventListener: (key, fn) => listeners.set(key, fn),
        removeEventListener: key => listeners.delete(key),
    };
    const window = {
        addEventListener: (key, fn) => listeners.set(key, fn),
        removeEventListener: key => listeners.delete(key),
    };
    const mount = runInNewContext(source.replace('export default function', '(function') + ')', {
        document, window, performance: {now: () => now},
        setInterval: fn => { interval = fn; return 1; },
        clearInterval: () => { interval = undefined; },
        IntersectionObserver: class {
            constructor(callback) { observer = callback; }
            observe() {}
            disconnect() { disconnected = true; }
        },
    });
    return {
        events, document, listeners,
        mount: (navigation_id = 'first') => mount({
            parentElement: {querySelector: () => marker},
            data: {navigation_id, content_id: '1', person_id: null,
                   required_seconds: 10, rule_version: 'active-dwell-bottom-v1'},
            setStateValue: (key, value) => events.push({key, value}),
        }),
        advance: (seconds = 1) => { for (let n = 0; n < seconds; n++) { now += 1000; interval?.(); } },
        end: visible => observer([{isIntersecting: visible, intersectionRatio: visible ? 1 : 0}]),
        focus: value => { focused = value; listeners.get(value ? 'focus' : 'blur')?.(); },
        belowArticle: () => { bottom = -1; },
        get disconnected() { return disconnected; },
        oldTimer: () => interval,
    };
}

test('requires dwell and currently visible end; emits once', () => {
    const b = browser(); b.mount(); b.end(true); b.advance(3);
    assert.equal(b.events.length, 0);
    b.end(false); b.advance(10);
    assert.equal(b.events.length, 0);
    b.end(true);
    assert.equal(b.events.length, 1);
    assert.equal(b.events[0].value.active_seconds, 13);
    b.advance(10);
    assert.equal(b.events.length, 1);
});

test('hidden and unfocused time is excluded', () => {
    const b = browser(); b.mount(); b.end(true);
    b.document.visibilityState = 'hidden'; b.advance(20);
    b.document.visibilityState = 'visible'; b.focus(false); b.advance(20);
    assert.equal(b.events.length, 0);
    b.focus(true); b.advance(10);
    assert.equal(b.events.length, 1);
    assert.equal(b.events[0].value.active_seconds, 10);
});

test('rerenders preserve dwell; cleanup removes observers and listeners', () => {
    const b = browser(); const cleanup = b.mount(); b.advance(6); cleanup();
    assert.equal(b.disconnected, true);
    assert.equal(b.listeners.size, 0);
    b.mount(); b.end(true); b.advance(4);
    assert.equal(b.events.length, 1);
});

test('new navigation resets time and fences old callbacks', () => {
    const b = browser(); b.mount(); b.end(true); b.advance(9);
    const staleTick = b.oldTimer();
    b.mount('second'); b.end(true); b.advance(1); staleTick();
    assert.equal(b.events.length, 0);
    b.advance(9);
    assert.equal(b.events.length, 1);
    assert.equal(b.events[0].value.navigation_id, 'second');
});

test('time below the article does not count', () => {
    const b = browser(); b.mount(); b.end(true); b.belowArticle(); b.advance(20);
    assert.equal(b.events.length, 0);
});
