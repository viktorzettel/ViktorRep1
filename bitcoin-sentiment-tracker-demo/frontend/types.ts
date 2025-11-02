export interface GroundingChunkWeb {
  uri: string;
  title: string;
}

export interface GroundingChunk {
  web: GroundingChunkWeb;
}

/**
 * The structure returned by the back‑end sentiment API.
 *
 * - `sentimentText` is the Markdown returned by Gemini.
 * - `score` is a 1–10 numeric sentiment score.
 * - `sources` lists the articles used to ground the sentiment.
 */
export interface SentimentData {
  sentimentText: string;
  score: number;
  sources: GroundingChunk[];
}