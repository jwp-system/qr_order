/* static/js/staff/board_live.js — 직원 보드 실시간 갱신: SSE 변경 신호 -> HTMX 갱신, 상대 시간 표시 갱신
 * 의존: htmx (전역), live_events.js, #board-container[data-events-url] */
(() => {
    const container = document.getElementById('board-container');
    if (!container || !container.dataset.eventsUrl) return;

    const dot = document.getElementById('live-dot');
    const refresh = () => htmx.trigger(container, 'board-refresh');

    function setLive(on) {
        if (!dot) return;
        dot.classList.toggle('animate-pulse', on);
        dot.classList.toggle('opacity-40', !on);
    }

    LiveEvents.connect(container.dataset.eventsUrl, {
        onChange: refresh,
        onSync: refresh,  // 최초 연결 사이에 놓친 변경도 보정하기 위해 항상 갱신
        onState: setLive,
    });

    // 백그라운드 탭에서 돌아오면 한 번 맞춰 본다
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') refresh();
    });

    // ---- 상대 시간 (폴링이 없으므로 클라이언트에서 갱신; 서버 time_ago 필터와 같은 규칙) ----
    function timeAgo(iso) {
        const sec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
        if (sec < 60) return '방금 전';
        if (sec < 3600) return `${Math.floor(sec / 60)}분 전`;
        if (sec < 86400) return `${Math.floor(sec / 3600)}시간 전`;
        return `${Math.floor(sec / 86400)}일 전`;
    }
    function tickTimes() {
        document.querySelectorAll('.time-ago[data-created-at]').forEach(el => {
            el.textContent = timeAgo(el.dataset.createdAt);
        });
    }
    setInterval(tickTimes, 30000);
    document.addEventListener('htmx:afterSettle', tickTimes);
})();
