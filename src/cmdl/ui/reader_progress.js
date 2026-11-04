// Mounted at the article end. Only this component's sentinel is observed.
export default function({parentElement, data, setStateValue}) {
    const marker = parentElement.querySelector('[data-reading-end]');
    const storeKey = '__cmdlReadingProgress';
    let progress = window[storeKey];
    if (!progress || progress.navigationId !== data.navigation_id) {
        progress = {navigationId: data.navigation_id, seconds: 0, sent: false};
        window[storeKey] = progress;
    }
    let stopped = false;
    let endVisible = false;
    let last = performance.now();
    const visible = () => document.visibilityState === 'visible' && document.hasFocus()
        && marker.getBoundingClientRect().bottom > 0;
    let wasVisible = visible();

    function tick() {
        if (stopped || window[storeKey] !== progress || !marker.isConnected) return;
        const now = performance.now();
        const isVisible = visible();
        // Don't credit time across hidden tabs, lost focus or suspended timers.
        if (wasVisible && isVisible) progress.seconds += Math.min((now - last) / 1000, 1.5);
        last = now;
        wasVisible = isVisible;
        if (!progress.sent && isVisible && endVisible && progress.seconds >= data.required_seconds) {
            progress.sent = true;
            setStateValue('completion', {
                navigation_id: data.navigation_id,
                person_id: data.person_id,
                content_id: data.content_id,
                active_seconds: Math.floor(progress.seconds),
                end_visible: true,
                rule_version: data.rule_version,
            });
        }
    }
    const observer = new IntersectionObserver(entries => {
        endVisible = entries.some(entry => entry.isIntersecting && entry.intersectionRatio >= 0.99);
        tick();
    }, {threshold: [0, 1]});
    observer.observe(marker);
    const timer = setInterval(tick, 1000);
    document.addEventListener('visibilitychange', tick);
    window.addEventListener('focus', tick);
    window.addEventListener('blur', tick);
    return () => {
        stopped = true;
        clearInterval(timer);
        observer.disconnect();
        document.removeEventListener('visibilitychange', tick);
        window.removeEventListener('focus', tick);
        window.removeEventListener('blur', tick);
    };
}
