from app.agent.sanitize import strip_images


def test_images_are_removed_outside_code():
    md = (
        'A ![chart](https://x/y.png "t") B ![](http://e/?d=1) C ![ref][img1]\n'
        "<IMG SRC='https://e/p'> inline `![k](v)`\n```\n![block](z)\n```"
    )
    out = strip_images(md)
    assert "https://x" not in out and "http://e" not in out and "IMG" not in out
    assert "A [image: chart] B  C [image: ref]" in out
    assert "`![k](v)`" in out and "![block](z)" in out


def test_links_and_citations_survive():
    md = "See [the docs](https://docs.example) and [1][2]."
    assert strip_images(md) == md


def test_tool_names_cited_as_sources_are_dropped():
    from app.agent.sanitize import strip_tool_markers

    text = "Customers and benefits providers, not vendors [list_entities]. Checked [Query Records]."
    assert strip_tool_markers(text) == "Customers and benefits providers, not vendors. Checked."
    kept = "Real citations [2], an editor's [sic], and code `x[scan]` stay."
    assert strip_tool_markers(kept) == kept
