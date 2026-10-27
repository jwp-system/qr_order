/* static/js/staff/login.js — 로그인 화면: PIN 키패드(4자리 입력 시 자동 제출), 매장 코드 기억
 * 의존: #login-form, #pin(hidden), #service_code, [data-pin-dot], [data-key] */
(() => {
    const form = document.getElementById('login-form');
    if (!form) return;

    const PIN_LENGTH = 4;
    const STORAGE_KEY = 'qo_service_code';
    const pinInput = document.getElementById('pin');
    const codeInput = document.getElementById('service_code');
    const dots = document.querySelectorAll('[data-pin-dot]');
    const dotsBox = document.getElementById('pin-dots');
    let pin = '';
    let submitting = false;

    // ---- 매장 코드 기억 (localStorage 실패는 무시) ----
    function loadCode() {
        try { return localStorage.getItem(STORAGE_KEY) || ''; } catch (_) { return ''; }
    }
    function saveCode(value) {
        try { localStorage.setItem(STORAGE_KEY, value); } catch (_) { /* 사생활 보호 모드 등 */ }
    }
    if (!codeInput.value) codeInput.value = loadCode();
    codeInput.addEventListener('input', () => { codeInput.value = codeInput.value.toUpperCase(); });
    form.addEventListener('submit', () => saveCode(codeInput.value.trim()));

    // ---- PIN ----
    function render() {
        dots.forEach((dot, i) => { dot.dataset.filled = i < pin.length ? 'true' : 'false'; });
        pinInput.value = pin;
    }

    function reset() {
        pin = '';
        submitting = false;
        render();
    }

    function submit() {
        if (!form.checkValidity()) {   // 매장 코드 비어 있음 등
            form.reportValidity();
            reset();
            return;
        }
        submitting = true;
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
    }

    function press(key) {
        if (submitting) return;
        if (key === 'back') pin = pin.slice(0, -1);
        else if (key === 'clear') pin = '';
        else if (/^\d$/.test(key) && pin.length < PIN_LENGTH) pin += key;
        render();
        if (pin.length === PIN_LENGTH) submit();
    }

    // 키패드 (이벤트 위임)
    document.addEventListener('click', (e) => {
        const btn = e.target.closest && e.target.closest('[data-key]');
        if (btn) press(btn.dataset.key);
    });

    // 물리 키보드: 매장 코드 입력 중이 아닐 때만 PIN으로 처리
    document.addEventListener('keydown', (e) => {
        const el = document.activeElement;
        if (el && el.tagName === 'INPUT' && el.type === 'text') return;
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        if (/^\d$/.test(e.key)) { press(e.key); e.preventDefault(); }
        else if (e.key === 'Backspace') { press('back'); e.preventDefault(); }
    });

    // 뒤로 가기(bfcache)로 돌아왔을 때 입력 상태 초기화
    window.addEventListener('pageshow', (e) => { if (e.persisted) reset(); });

    // 서버가 오류로 다시 그린 경우: 점을 흔들어 알리고, 코드가 있으면 바로 PIN 입력 가능
    if (document.getElementById('login-error') && dotsBox) {
        dotsBox.classList.add('shake');
        if (codeInput.value) codeInput.blur();
    } else if (!codeInput.value) {
        codeInput.focus();
    }
})();
