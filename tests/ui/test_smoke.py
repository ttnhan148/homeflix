def test_home_loads_with_grid_and_no_console_errors(open_app):
    page = open_app()
    page.goto("http://homeflix.test/")
    page.wait_for_selector("#homeGridContent .movie-card")
    assert page.console_errors == []
    assert any(r.startswith("GET /api/home/latest") for r in page.requests)
