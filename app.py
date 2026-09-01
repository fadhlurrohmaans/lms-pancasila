<script>
(function() {
    const parentWin = window.parent;
    const parentDoc = parentWin.document;

    // Cegah duplikasi script saat Streamlit rerun
    if (parentWin.__antiCheatLoaded) return;
    parentWin.__antiCheatLoaded = true;

    let lastTriggerViolation = 0;

    function getBtnByText(text) {
        const buttons = Array.from(parentDoc.querySelectorAll('button'));
        return buttons.find(b => b.innerText.includes(text));
    }

    function hideActionButtons() {
        const btn = getBtnByText('Catat Pelanggaran');
        if (btn && btn.parentElement) {
            const container = btn.closest('[data-testid="stElementContainer"]') || btn.parentElement;
            if (container && container.style.display !== 'none') {
                container.style.display = 'none';
            }
        }
    }

    setInterval(hideActionButtons, 500);

    function triggerViolation() {
        const now = Date.now();
        if (now - lastTriggerViolation < 3000) return;
        lastTriggerViolation = now;
        const triggerBtn = getBtnByText('Catat Pelanggaran');
        if (triggerBtn) triggerBtn.click();
    }

    parentDoc.addEventListener('visibilitychange', function() {
        if (parentDoc.hidden) { triggerViolation(); }
    });

    parentWin.addEventListener('blur', function() {
        triggerViolation();
    });
})();
</script>
