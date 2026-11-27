// Count only cards at least half visible for one continuous, focused second.
export default function({data, setStateValue}) {
    const store = '__cmdlFeedVisibility';
    let progress = window[store];
    if (!progress || progress.feedId !== data.feed_id) {
        progress = {feedId: data.feed_id, seen: new Set()};
        window[store] = progress;
    }
    const targets = new Map();
    let stopped = false;
    const focused = () => document.visibilityState === 'visible' && document.hasFocus();
    const tracking = () => !stopped && window[store] === progress;
    function resetVisibility() {
        if (!tracking()) return;
        const start = focused() ? performance.now() : null;
        for (const target of targets.values()) target.visibleSince = target.visible ? start : null;
    }
    const observer = new IntersectionObserver(entries => {
        if (!tracking()) return;
        for (const entry of entries) {
            const target = targets.get(entry.target);
            if (target) {
                target.visible = entry.isIntersecting && entry.intersectionRatio >= 0.5;
                target.visibleSince = target.visible && focused()
                    ? target.visibleSince ?? performance.now()
                    : null;
            }
        }
    }, {threshold: [0, 0.5, 1]});
    function tick() {
        if (!tracking()) return;
        const now = performance.now();
        const isFocused = focused();
        for (const card of data.cards) {
            const element = document.querySelector(card.selector);
            if (element && !targets.has(element)) {
                targets.set(element, {...card, visible: false, visibleSince: null});
                observer.observe(element);
            }
        }
        let changed = false;
        for (const [element, target] of targets) {
            if (!element.isConnected) {
                observer.unobserve(element);
                targets.delete(element);
                continue;
            }
            if (!target.visible || !isFocused) {
                target.visibleSince = null;
                continue;
            }
            target.visibleSince ??= now;
            if (now - target.visibleSince >= 1000 && !progress.seen.has(target.content_id)) {
                progress.seen.add(target.content_id);
                changed = true;
            }
        }
        if (changed) setStateValue('report', {
            feed_id: data.feed_id, content_ids: [...progress.seen],
        });
    }
    document.addEventListener('visibilitychange', resetVisibility);
    window.addEventListener('focus', resetVisibility);
    window.addEventListener('blur', resetVisibility);
    const timer = setInterval(tick, 250);
    tick();
    return () => {
        stopped = true;
        clearInterval(timer);
        observer.disconnect();
        document.removeEventListener('visibilitychange', resetVisibility);
        window.removeEventListener('focus', resetVisibility);
        window.removeEventListener('blur', resetVisibility);
    };
}
