import re


def escape_streamlit_markdown(text: str) -> str:
    """
    Escape raw dollar signs so Streamlit markdown does not interpret currency
    amounts as inline LaTeX and collapse spacing/font rendering.
    """
    return re.sub(r"(?<!\\)\$", r"\\$", text)
