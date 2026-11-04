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
    let last = performance.now();
    const observer = new IntersectionObserver(entries => {
        for (const entry of entries) {
            const target = targets.get(entry.target);
            if (target) {
                target.visible = entry.isIntersecting && entry.intersectionRatio >= 0.5;
                if (!target.visible) target.elapsed = 0;
            }
        }
    }, {threshold: [0, 0.5, 1]});
    function tick() {
        if (stopped || window[store] !== progress) return;
        const now = performance.now();
        const delta = Math.min(now - last, 500);
        last = now;
        for (const card of data.cards) {
            const element = document.querySelector(card.selector);
            if (element && !targets.has(element)) {
                targets.set(element, {...card, visible: false, elapsed: 0});
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
            if (!target.visible || document.visibilityState !== 'visible' || !document.hasFocus()) {
                target.elapsed = 0;
                continue;
            }
            target.elapsed += delta;
            if (target.elapsed >= 1000 && !progress.seen.has(target.content_id)) {
                progress.seen.add(target.content_id);
                changed = true;
            }
        }
        if (changed) setStateValue('report', {
            feed_id: data.feed_id, content_ids: [...progress.seen],
        });
    }
    const timer = setInterval(tick, 250);
    tick();
    return () => {
        stopped = true;
        clearInterval(timer);
        observer.disconnect();
    };
}
