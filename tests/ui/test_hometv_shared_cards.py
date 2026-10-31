"""buildPosterCard và buildContinueCard dùng chung giữa HomeFlix và HomeTV."""
URL = "http://homeflix.test/"


def test_poster_card_matches_movie_card_dom(app_module, open_app):
    page = open_app()
    page.goto(URL)
    got = page.evaluate("""() => {
        const c = buildMovieCard({slug: 'a', name: 'A', poster_url: '/p.jpg', year: 2020});
        return {cls: c.className, slug: c.dataset.slug, wrap: !!c.querySelector(':scope > .poster-wrap > img.movie-poster'),
                src: c.querySelector('img').getAttribute('src'), title: c.querySelector('.movie-info > .movie-title').textContent,
                meta: c.querySelector('.movie-info > .movie-meta').textContent};
    }""")
    assert got == {"cls": "movie-card", "slug": "a", "wrap": True, "src": "/p.jpg", "title": "A", "meta": "2020"}


def test_poster_card_calls_onclick_and_falls_back_on_image_error(app_module, open_app):
    page = open_app()
    page.goto(URL)
    got = page.evaluate("""() => {
        let n = 0;
        const c = buildPosterCard({title: 'T', meta: 'M', imgSrc: '/missing.png', onClick: () => { n++; }, focusable: true});
        document.body.appendChild(c);
        c.click();
        const img = c.querySelector('img');
        img.onerror();
        return {n, tab: c.getAttribute('tabindex'), fallback: img.src === POSTER_PLACEHOLDER || img.getAttribute('src') === POSTER_PLACEHOLDER};
    }""")
    assert got == {"n": 1, "tab": "0", "fallback": True}


def test_continue_card_custom_handlers_no_swipe_no_progress(app_module, open_app):
    page = open_app()
    page.goto(URL)
    got = page.evaluate("""() => {
        let n = 0;
        const row = buildContinueCard({slug: 'ch-1', name: 'K1'}, 'home-cont is-lead', {
            poster: () => '/l.png', meta: () => 'Tin tức', pct: () => 0, label: () => 'Mở K1', onPlay: () => { n++; }, swipe: false});
        document.body.appendChild(row);
        const card = row.matches('button') ? row : row.querySelector('button.home-cont');
        card.click();
        return {n, swipeActs: document.querySelectorAll('.swipe-act').length, bar: !!row.querySelector('.continue-progress'),
                label: card.getAttribute('aria-label'), meta: row.querySelector('.continue-ep').textContent,
                src: row.querySelector('img').getAttribute('src')};
    }""")
    assert got == {"n": 1, "swipeActs": 0, "bar": False, "label": "Mở K1", "meta": "Tin tức", "src": "/l.png"}
