/* static/js/live_events.js — 실시간 이벤트(SSE) 공통 연결 헬퍼. 직원 보드와 고객 화면이 함께 사용한다.
 *
 * LiveEvents.connect(url, {
 *   onChange(),        // 주문 변경 신호
 *   onSync(isFirst),   // 연결/재연결 직후 신호. isFirst=true면 페이지 최초 연결
 *   onState(isLive),   // 연결 상태 변화 (표시용)
 * })
 * 일시적인 끊김은 EventSource가 자동 재연결한다. 서버가 응답을 거부해 완전히 닫힌 경우만
 * 5초 간격으로 다시 시도하고, 연속 5번 실패하면 중단한다.
 */
window.LiveEvents = {
    connect(url, { onChange, onSync, onState } = {}) {
        let source = null;
        let seenSync = false;
        let failures = 0;

        function open() {
            source = new EventSource(url);
            source.addEventListener('open', () => {
                failures = 0;
                if (onState) onState(true);
            });
            source.addEventListener('order_changed', () => { if (onChange) onChange(); });
            source.addEventListener('sync', () => {
                const isFirst = !seenSync;
                seenSync = true;
                if (onSync) onSync(isFirst);
            });
            source.addEventListener('error', () => {
                if (onState) onState(false);
                if (source.readyState === EventSource.CLOSED) {
                    failures += 1;
                    if (failures <= 5) setTimeout(open, 5000);
                }
            });
        }

        open();
        window.addEventListener('beforeunload', () => { if (source) source.close(); });
        return { close: () => { if (source) source.close(); } };
    },
};
