import React, { useState, useCallback } from 'react';
import { fetchBitcoinSentiment } from './services/geminiService';
import type { SentimentData } from './types';
import BtcIcon from './components/icons/BtcIcon';
import LoadingSpinner from './components/LoadingSpinner';
import SourceCard from './components/SourceCard';

// This is a simple markdown-to-html converter for this specific app's needs.
// It handles ### headers, bullet points (*), and bold text (**text**).
const SimpleMarkdown: React.FC<{ text: string }> = ({ text }) => {
  const html = text
    .split('\n')
    .map(line => line.trim())
    .filter(line => line.length > 0)
    .map((line, index) => {
      if (line.startsWith('### ')) {
        return `<h3 class="text-lg font-semibold text-amber-400 mt-4 mb-2">${line.substring(4)}</h3>`;
      }
      if (line.startsWith('* ')) {
        const item = line.substring(2)
          .replace(/\*\*(.*?)\*\*/g, '<strong class="font-semibold text-slate-200">$1</strong>');
        return `<li class="ml-5 list-disc">${item}</li>`;
      }
      const paragraph = line.replace(/\*\*(.*?)\*\*/g, '<strong class="font-semibold text-slate-200">$1</strong>');
      return `<p class="mb-2">${paragraph}</p>`;
    })
    .join('');

  return <div className="prose-sm text-slate-300" dangerouslySetInnerHTML={{ __html: html }} />;
};

const App: React.FC = () => {
  const [sentimentData, setSentimentData] = useState<SentimentData | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const handleFetchSentiment = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    setSentimentData(null);
    try {
      const data = await fetchBitcoinSentiment();
      setSentimentData(data);
    } catch (err) {
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("An unexpected error occurred.");
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  return (
    <div className="min-h-screen bg-slate-900 text-white flex flex-col items-center justify-center p-4 sm:p-6 font-sans">
      <main className="w-full max-w-2xl mx-auto">
        <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700 rounded-xl shadow-2xl shadow-slate-950/50 p-6 sm:p-8">
          <div className="text-center">
            <BtcIcon className="w-16 h-16 mx-auto text-amber-400 mb-4" />
            <h1 className="text-3xl sm:text-4xl font-bold text-slate-100">
              Bitcoin Sentiment Tracker
            </h1>
            <p className="text-slate-400 mt-2">
              Get today's Bitcoin market sentiment analysis powered by Gemini.
            </p>
          </div>

          <div className="mt-8 text-center">
            <button
              onClick={handleFetchSentiment}
              disabled={isLoading}
              className="bg-amber-500 hover:bg-amber-600 disabled:bg-slate-600 disabled:cursor-not-allowed text-slate-900 font-bold py-3 px-8 rounded-full transition-transform duration-200 transform hover:scale-105 focus:outline-none focus:ring-4 focus:ring-amber-500/50"
            >
              {isLoading ? "Analyzing..." : "Get Today's Sentiment"}
            </button>
          </div>

          <div className="mt-8 min-h-[10rem]">
            {isLoading && <LoadingSpinner />}
            {error && (
              <div className="bg-red-900/50 border border-red-700 text-red-300 px-4 py-3 rounded-lg" role="alert">
                <strong className="font-bold">Error: </strong>
                <span className="block sm:inline">{error}</span>
              </div>
            )}
            {sentimentData && (
              <div className="animate-fade-in space-y-6">
                <div className="p-4 bg-slate-900/60 rounded-lg">
                  <SimpleMarkdown text={sentimentData.sentimentText} />
                </div>
                
                {sentimentData.sources.length > 0 && (
                  <div>
                    <h3 className="text-lg font-semibold text-amber-400 mb-3">
                      Information Sources
                    </h3>
                    <div className="space-y-2">
                      {sentimentData.sources.map((source, index) => (
                        <SourceCard key={index} source={source} />
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
        <footer className="text-center mt-8">
            <p className="text-sm text-slate-500">Powered by Gemini with Google Search grounding.</p>
        </footer>
      </main>
      <style>{`
        .animate-fade-in {
          animation: fadeIn 0.5s ease-in-out;
        }
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
};

export default App;
