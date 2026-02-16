# Excel Agent

A specialized AI assistant designed to help users with Excel tasks, ranging from simple formula generation to complex data analysis.

## Product Specification

### Core Concept
The Excel Agent uses a **dual-model architecture** to optimize for both speed and depth:
*   **Fast Model**: Handles quick, transactional queries like "How do I do a VLOOKUP?" or "Sum column A". It prioritizes low latency.
*   **Thinking Model**: Handles complex reasoning tasks like "Analyze the trend in this sales data" or "Why is this formula returning an error?". It simulates a "thinking" process to provide deep, structured insights.

### Architecture
*   **Frontend**: lightweight HTML/JS interface with real-time feedback.
*   **Backend**: Python (FastAPI) server that acts as the "Agent Router".
*   **Router Logic**: Analyzes user intent to dynamically select the appropriate model (Fast vs. Thinking).

### Roadmap
*   **Phase 1 (Current)**: Web-based chat interface with mock model routing.
*   **Phase 2**: Integration with real LLMs (OpenAI/Gemini).
*   **Phase 3**: Excel Add-in development for direct spreadsheet manipulation.

## Setup & Running

1.  **Prerequisites**: Python 3.x installed.
2.  **Installation**:
    ```bash
    pip install -r requirements.txt
    ```
3.  **Run Locally**:
    *   Double-click `start_server.bat` (Windows)
    *   Or run: `uvicorn main:app --reload`
4.  **Access**: Open `http://127.0.0.1:8000` in your browser.
