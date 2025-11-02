import type { SentimentData } from '../types';

/**
 * Fetch today's Bitcoin sentiment from the back‑end API.
 *
 * Throws an error if the request fails.  The returned object includes a
 * numeric score (1–10), the sentiment text in Markdown and the list of
 * sources.
 */
export async function fetchSentiment(): Promise<SentimentData> {
  const response = await fetch('/api/sentiment');
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  }
  return (await response.json()) as SentimentData;
}