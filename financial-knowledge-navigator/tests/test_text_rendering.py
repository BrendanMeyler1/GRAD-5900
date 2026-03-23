from backend.core.text_rendering import escape_streamlit_markdown


def test_escape_streamlit_markdown_escapes_currency_dollars():
    text = "Revenue was $72,480 million and profit was $5,000 million."

    assert escape_streamlit_markdown(text) == (
        "Revenue was \\$72,480 million and profit was \\$5,000 million."
    )


def test_escape_streamlit_markdown_keeps_existing_escapes():
    text = "Already escaped \\$100 and new $200"

    assert escape_streamlit_markdown(text) == "Already escaped \\$100 and new \\$200"
