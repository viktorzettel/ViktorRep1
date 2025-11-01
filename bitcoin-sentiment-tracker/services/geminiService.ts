import { GoogleGenAI } from "@google/genai";
import type { SentimentData, GroundingChunk } from '../types';

export async function fetchBitcoinSentiment(): Promise<SentimentData> {
  try {
    if (!process.env.API_KEY) {
      throw new Error("API_KEY environment variable not set.");
    }
    const ai = new GoogleGenAI({ apiKey: process.env.API_KEY });

    const prompt = `
    Provide a concise, daily sentiment analysis for Bitcoin (BTC) for today, formatted as Markdown.
    Your analysis should be grounded in the latest available information.

    Consider the following factors:
    - Recent price action and volatility.
    - Significant news events (e.g., regulatory updates, major adoptions, security breaches).
    - Social media trends and general public perception.
    - Market indicators (e.g., trading volume, fear & greed index if accessible).
    
    Structure your response with the following sections using Markdown:
    ### Overall Sentiment
    A single word or short phrase (e.g., Bullish, Bearish, Neutral, Cautiously Optimistic).
    
    ### Summary
    A short paragraph (2-4 sentences) explaining the reasoning behind the sentiment.
    
    ### Key Drivers
    2-3 bullet points highlighting the main factors influencing the sentiment today.
    `;

    const response = await ai.models.generateContent({
      model: "gemini-2.5-flash",
      contents: prompt,
      config: {
        tools: [{ googleSearch: {} }],
      },
    });

    const sentimentText = response.text;
    const groundingMetadata = response.candidates?.[0]?.groundingMetadata;
    
    // Ensure we only get chunks that have a 'web' property with a URI and title
    const sources: GroundingChunk[] = groundingMetadata?.groundingChunks?.filter(
      chunk => chunk.web && chunk.web.uri && chunk.web.title
    ) ?? [];

    if (!sentimentText) {
      throw new Error("The API returned an empty sentiment text.");
    }

    return { sentimentText, sources };
  } catch (error) {
    console.error("Error fetching Bitcoin sentiment:", error);
    if (error instanceof Error) {
        throw new Error(`Failed to fetch sentiment: ${error.message}`);
    }
    throw new Error("An unknown error occurred while fetching sentiment.");
  }
}
