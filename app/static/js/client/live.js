/* static/js/client/live.js — 고객 주문 화면 실시간 갱신: 우리 테이블의 주문이 바뀌면
 *  1) body로 'table-refresh' 이벤트를 발생시킨다 (헤더 배지 등이 hx-trigger="table-refresh from:body"로 구독)
 *  2) 주문 현황 모달이 열려 있으면 그 내용을 다시 가져온다.
 * 의존: htmx (전역), live_events.js, #live-config[data-events-url] */
(() => {
    const cfg = document.getElementById('live-config');
    if (!cfg || !cfg.dataset.eventsUrl) return;

    const LIVE_HEADER = { 'X-Live-Refresh': '1' };  // 우리가 보낸 갱신 요청 표식

    // ---- 주문 현황 모달: 마지막으로 연 요청을 기억해 두었다가 열려 있으면 갱신 ----
    // #modal-container 는 옵션/장바구니/주문완료 모달도 함께 쓰므로, 같은 컨테이너에
    // 다른 모달이 로드되면 기억을 버린다 (그렇지 않으면 다른 모달을 주문 현황으로 덮어쓴다).
    let lastStatusReq = null;  // { path, target, swap }
    document.body.addEventListener('htmx:afterRequest', (e) => {
        const req = e.detail.requestConfig || {};
        const headers = req.headers || {};
        if (headers['X-Live-Refresh']) return;  // 우리가 보낸 갱신 요청은 기록하지 않는다
        if (!e.detail.successful || !req.path) return;

        if (req.path.startsWith('/orders/status')) {
            const elt = req.elt;
            lastStatusReq = {
                path: req.path,
                target: e.detail.target,
                swap: (elt && elt.getAttribute('hx-swap')) || 'innerHTML',
            };
        } else if (lastStatusReq && e.detail.target === lastStatusReq.target) {
            lastStatusReq = null;
        }
    });

    function refreshStatusModal() {
        const r = lastStatusReq;
        if (!r || !r.target || !document.body.contains(r.target)) return;
        if (!r.target.firstElementChild) return;          // 닫혀서 비어 있음
        if (r.swap.startsWith('outerHTML')) return;      // 대상 요소 자체가 교체되는 방식은 제외
        htmx.ajax('GET', r.path, { target: r.target, swap: r.swap, headers: LIVE_HEADER });
    }

    function refresh() {
        htmx.trigger(document.body, 'table-refresh');
        refreshStatusModal();
    }

    LiveEvents.connect(cfg.dataset.eventsUrl, {
        onChange: refresh,
        // 페이지를 막 열었을 때는 서버가 방금 그린 화면이므로 건너뛰고, 재연결일 때만 맞춘다
        onSync: (isFirst) => { if (!isFirst) refresh(); },
    });

    // 백그라운드(모바일 잠금 등)에서 돌아오면 한 번 맞춰 본다
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') refresh();
    });
})();
