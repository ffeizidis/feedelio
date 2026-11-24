"""trafilatura's link mode can drop prose; extraction must not lose article text."""

from feedelio.core.content import extract, plain

# Blogger's post markup: prose sits loose in a <div>, with inline links between runs of text.
PROSE = (
    "In 1974, the journalist Linda Rosencrantz interviewed her friend, the photographer "
    "Peter Hujar, for what was intended as a foundational chapter in a proposed book. "
) * 3
PAGE = f"""<html><head><title>Post</title></head><body>
<div class="post-body entry-content" itemprop="articleBody">
<div style="text-align: left;"><p><br/></p>{PROSE}Ira Sachs, the indie helmer
<a href="https://example.com/a">with a longstanding interest</a>
<a href="https://example.com/b">in the bohemian</a>, has now adapted that transcript as a film.
<p>{PROSE}</p></div></div></body></html>""".encode()


def test_link_extraction_keeps_loose_prose():
    url = "https://blog.example.com/2026/11/post.html"
    body = extract(url, document=(PAGE, url, {}), transcripts=False)["body"]
    text = plain(body)
    assert "In 1974, the journalist Linda Rosencrantz" in text
    assert "Ira Sachs, the indie helmer" in text
