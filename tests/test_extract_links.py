"""trafilatura's link mode can drop prose; extraction must not lose article text."""

from feedelio.core.content import extract, plain

# Blogger's post markup: prose sits loose in a <div>, with inline links between runs of text.
PROSE = (
    "In 1974, the journalist Linda Rosencrantz interviewed her friend, the photographer "
    "Peter Hujar, for what was intended as a foundational chapter in a proposed book. "
) * 3
PAGE = f"""<html><head><title>Post</title></head><body>
<div class="post-body entry-content" itemprop="articleBody">
<div style="text-align: left;"><div class="separator"><a href="https://img.example.com/s1920/still.jpg"><img src="https://img.example.com/w400/still.jpg" width="400" height="293"/></a></div><span style="font-family: georgia;"><div><span><br/></span></div>{PROSE}Ira Sachs, the indie helmer
<a href="https://example.com/a">with a longstanding interest</a>
<a href="https://example.com/b">in the bohemian</a>, has now adapted that transcript as a film.
<p>{PROSE}</p></span><div><span><i>Out now on DVD.</i></span></div></div></div></body></html>""".encode()


def test_link_extraction_keeps_loose_prose_images_and_links():
    url = "https://blog.example.com/2026/11/post.html"
    body = extract(url, document=(PAGE, url, {}), transcripts=False)["body"]
    text = plain(body)
    assert "In 1974, the journalist Linda Rosencrantz" in text
    assert "Ira Sachs, the indie helmer" in text
    assert "Out now on DVD." in text
    assert "https://img.example.com/w400/still.jpg" in body
    assert 'href="https://example.com/a"' in body
