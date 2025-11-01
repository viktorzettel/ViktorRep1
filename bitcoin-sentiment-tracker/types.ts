export interface GroundingChunkWeb {
  uri: string;
  title: string;
}

export interface GroundingChunk {
  web: GroundingChunkWeb;
}

export interface SentimentData {
  sentimentText: string;
  sources: GroundingChunk[];
}
