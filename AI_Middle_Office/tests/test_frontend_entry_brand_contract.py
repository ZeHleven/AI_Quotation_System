from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_frontend(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_vite_shell_uses_the_canonical_brand_metadata():
    html = read_frontend("ai-web/index.html")

    assert '<html lang="zh-CN">' in html
    assert "旗胜智价 · 企业智能经营平台" in html
    assert "/static/brand-favicon.svg" in html
    assert "<title>ai-web</title>" not in html


def test_legacy_business_pages_use_the_same_brand_and_login_entry():
    quote_html = read_frontend("index.html")
    admin_html = read_frontend("admin.html")
    shared_js = read_frontend("static/js/shared.js")

    assert '<span class="sys-logo-mark">QS</span>' in quote_html
    assert '<span class="sys-logo-mark">QS</span>' in admin_html
    assert "验证接口 Token" not in quote_html
    assert "window.location.replace(loginUrl())" in quote_html
    assert "window.location.replace(loginUrl())" in admin_html
    assert "const loginUrl" in shared_js
    assert "window.location.hash" in shared_js
    assert "/static/js/shared.js?v=20260710-entry-brand" in quote_html
    assert "/static/js/shared.js?v=20260710-entry-brand" in admin_html


def test_legacy_portal_no_longer_contains_unverified_marketing_metrics():
    html = read_frontend("app.html")

    assert '<span class="nav-logo-mark">QS</span>' in html
    assert '<span class="footer-logo-mark">QS</span>' in html
    assert "fetch('/login'" in html
    assert "window.location.replace('/login')" in html
    assert "60<em>s</em>" not in html
    assert "40<em>+</em>" not in html
    assert "95<em>%</em>" not in html
    assert "北京 2024 年市场价格库" not in html


def test_vite_login_rejects_unsafe_redirect_targets():
    app = read_frontend("ai-web/src/App.vue")

    assert "function safeRedirectPath(value)" in app
    assert "candidate.startsWith('//')" in app
    assert "candidate.includes('\\\\')" in app
    assert "target.origin !== window.location.origin" in app
    assert "['/login', '/app.html'].includes(target.pathname)" in app
    assert "window.location.replace(landingPath(me))" in app


def test_vite_navigation_is_grouped_by_work_context_without_development_labels():
    app = read_frontend("ai-web/src/App.vue")

    for group in ("核心工作台", "业务协同", "数据资产", "智能工具", "系统管理"):
        assert f'<p class="nav-group-label">{group}</p>' in app
    assert "旧版入口" not in app
    assert "<span>新建报价</span>" in app
    assert "<span>经营总览</span>" in app
    assert "<span>我的项目任务</span>" in app
    assert '<span class="nav-status-badge">试运行</span>' not in app
    assert '<span class="nav-status-badge is-muted">兼容</span>' not in app
    assert "BIZ-2x · DWG Trial" not in app
    assert '<p class="eyebrow">Phase' not in app


def test_vite_login_prefers_server_role_default_with_a_frontend_fallback():
    app = read_frontend("ai-web/src/App.vue")

    assert "const ROLE_DEFAULT_HOME_RULES" in app
    assert "'/admin/project-tasks/my'" in app
    assert "safeRedirectPath(user?.default_home_path)" in app
    assert "roleDefaultHomePath(user)" in app
    assert "canUsePostLoginPath(user, redirect)" in app
    assert "return firstModule?.path || '/no-access'" in app
    assert "尚未分配可用模块" in app


def test_unified_quote_entry_is_a_permission_guarded_handoff_not_a_second_quote_api():
    app = read_frontend("ai-web/src/App.vue")
    legacy_quote = read_frontend("index.html")

    assert "if (pathname === '/quote/new') return availablePaths.has('/index.html')" in app
    assert "if (pathname === '/quote/new') return 'quoteNew'" in app
    assert "@click=\"openQuickQuote('quick')\"" in app
    assert "function openQuickQuote(mode = 'quick')" in app
    assert "new URLSearchParams({ entry: 'new-quote' })" in app
    assert "openLegacy(`/index.html?${params.toString()}`)" in app
    assert "navigate('/admin/requirement-standardization?entry=new-quote')" in app
    assert "routeName.value === 'quoteNew'" in app
    assert "return params.get('entry') === 'new-quote';" in legacy_quote
    assert "const prepareUnifiedNewQuoteEntry" in legacy_quote
    assert "document.querySelector('.input-textarea-wrap textarea')?.focus();" in legacy_quote

    entry_start = legacy_quote.index("const isUnifiedNewQuoteEntry")
    entry_end = legacy_quote.index("const parsePreviewPayload", entry_start)
    entry_block = legacy_quote[entry_start:entry_end]
    assert "/quote/jobs" not in entry_block
    assert "quote_job_id" not in entry_block
