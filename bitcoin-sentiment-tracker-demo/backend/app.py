"""
Simple Flask back‑end for a Bitcoin sentiment demo.

This server fetches a handful of Bitcoin‑related news articles from public RSS
feeds, prompts Google’s Gemini model to generate a concise daily sentiment
analysis, extracts an overall sentiment label (Bullish/Bearish/Neutral) and
maps it to a 1–10 sentiment score.  It returns the numeric score, the
generated summary and a list of the source articles.

Before running, install the dependencies in requirements.txt and set
``GEMINI_API_KEY`` in your environment to your Gemini API key.

This file does not depend on the more complex ``sentiment.py`` pipeline; it
demonstrates a minimal end‑to‑end sentiment API that can later be replaced
with the full pipeline if desired.
"""

import os
import re
from typing import List, Dict

from flask import Flask, jsonify
import feedparser  # type: ignore

try:
    import google.generativeai as genai  # type: ignore
except ImportError:
    raise RuntimeError(
        "google-generativeai is not installed. Please add it to your backend/requirements.txt"
    )

API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")
if not API_KEY:
    raise RuntimeError("Set GEMINI_API_KEY or API_KEY in your environment")

# Configure the Gemini SDK once at startup
genai.configure(api_key=API_KEY)

app = Flask(__name__)


def fetch_articles(feeds: List[str], max_articles: int = 10) -> List[Dict[str, str]]:
    """Pull up to ``max_articles`` articles from the supplied RSS feed URLs.

    Each returned dict contains ``title``, ``link`` and ``summary`` keys.  We
    stop after collecting ``max_articles`` articles across all feeds.
    """
    articles: List[Dict[str, str]] = []
    for feed_url in feeds:
        parsed = feedparser.parse(feed_url)
        for entry in parsed.entries:
            if len(articles) >= max_articles:
                break
            title = getattr(entry, "title", "(untitled)")
            link = getattr(entry, "link", "")
            summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
            articles.append({"title": title.strip(), "link": link.strip(), "summary": summary.strip()})
        if len(articles) >= max_articles:
            break
    return articles


def call_gemini(prompt: str) -> str:
    """Call the Gemini model to generate content given a prompt.

    For safety and determinism, we specify a zero temperature and use
    the Google Search tool for grounding.  The returned text may include
    headers (### Overall Sentiment, ### Summary, ### Key Drivers)."""
    model = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content(
        prompt,
        tools=[{"googleSearch": {}}],
        generation_config={"temperature": 0.0},
    )
    return response.text if hasattr(response, "text") else ""


def parse_overall_sentiment(text: str) -> str:
    """Extract the overall sentiment word from the LLM's output.

    The model is instructed to include a section starting with '### Overall
    Sentiment' followed by a one‑word label (e.g. Bullish, Bearish, Neutral).
    If parsing fails, we default to 'Neutral'.
    """
    match = re.search(r"Overall\s+Sentiment\s*:?\s*(\w+)", text, re.IGNORECASE)
    return match.group(1) if match else "Neutral"


def map_sentiment_to_score(word: str) -> float:
    """Map a qualitative sentiment word to a 1–10 numeric score.

    'Bullish' → 10, 'Neutral' → 5, 'Bearish' → 1.  Other words default to 5.
    """
    lower = word.lower()
    if lower == "bullish":
        return 10.0
    if lower == "bearish":
        return 1.0
    if lower == "neutral":
        return 5.0
    # Unknown words are treated as neutral
    return 5.0


@app.route("/api/sentiment", methods=["GET"])
def get_sentiment() -> Dict[str, object]:
    """Endpoint: returns today's Bitcoin sentiment score and summary.

    It fetches up to five news articles from two popular Bitcoin feeds,
    constructs a prompt and asks Gemini to produce a sentiment analysis.
    The overall sentiment label is mapped to a 1–10 score and returned
    alongside the full summary and the list of article sources.
    """
    # Example feeds; replace or extend these as needed
    rss_feeds = [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://bitcoinmagazine.com/.rss",
    ]
    articles = fetch_articles(rss_feeds, max_articles=5)

    # Combine article titles and summaries into a single context for the model
    context_lines = []
    for art in articles:
        if art["summary"]:
            context_lines.append(f"- {art['title']}: {art['summary']}")
        else:
            context_lines.append(f"- {art['title']}")
    context = "\n".join(context_lines)

    prompt = f"""
Provide a concise, daily sentiment analysis for Bitcoin (BTC) for today, formatted as Markdown.
Your analysis should be grounded in the following recent news items:

{context}

Please include the following sections using Markdown:
### Overall Sentiment
A single word or short phrase (e.g., Bullish, Bearish, Neutral, Cautiously Optimistic).
### Summary
2–4 sentences explaining the reasoning behind the sentiment.
### Key Drivers
2–3 bullet points highlighting the main factors influencing today's sentiment.
"""

    llm_output = call_gemini(prompt)
    overall_word = parse_overall_sentiment(llm_output)
    score = map_sentiment_to_score(overall_word)

    # Transform articles into the format expected by the front‑end
    sources = [{"uri": art["link"], "title": art["title"]} for art in articles]

    return jsonify({
        "score": score,
        "sentimentText": llm_output.strip(),
        "sources": sources,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)