from tests.ui.test_three_taps import _open

MANIFEST_FAIL = "window.__hls.handlers.error('error', {fatal: true, type: 'networkError', details: 'manifestLoadError'})"


def _play(page):
    page.evaluate("document.querySelector('.hero-btn-play').click()")
    page.wait_for_function("window.__hlsSource")


def test_first_play_goes_through_proxy(open_app):
    page = _open(open_app)
    _play(page)
    assert page.evaluate("window.__hlsSource").startswith("/proxy/m3u8?url=")


def test_proxy_manifest_failure_retries_direct_and_remembers(open_app):
    page = _open(open_app)
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    _play(page)
    page.evaluate(MANIFEST_FAIL)
    page.wait_for_function("window.__hlsSource.startsWith('http://media.test/')")
    assert dialogs == []
    assert page.evaluate("Number(localStorage.getItem('hf_direct_stream_until')) > Date.now()")


def test_after_fallback_next_play_goes_direct(open_app):
    page = _open(open_app)
    page.evaluate("localStorage.setItem('hf_direct_stream_until', String(Date.now() + 60000))")
    _play(page)
    assert page.evaluate("window.__hlsSource").startswith("http://media.test/")


def test_direct_failure_reports_once(open_app):
    page = _open(open_app)
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.evaluate("localStorage.setItem('hf_direct_stream_until', String(Date.now() + 60000))")
    _play(page)
    page.evaluate(MANIFEST_FAIL)
    page.wait_for_function("document.getElementById('playerError') && !document.getElementById('playerError').hidden")
    assert dialogs == []
