import React from 'react';
import type { GroundingChunk } from '../types';

interface SourceCardProps {
  source: GroundingChunk;
}

const SourceCard: React.FC<SourceCardProps> = ({ source }) => {
  return (
    <a
      href={source.web.uri}
      target="_blank"
      rel="noopener noreferrer"
      className="block p-3 bg-slate-700/50 hover:bg-slate-700 rounded-lg transition-colors duration-200 group"
    >
      <div className="flex items-center space-x-3">
        <div className="flex-shrink-0">
          <svg
            className="w-5 h-5 text-slate-400 group-hover:text-amber-400"
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="currentColor"
            aria-hidden="true"
          >
            <path
              fillRule="evenodd"
              d="M12.586 4.586a2 2 0 112.828 2.828l-3 3a2 2 0 01-2.828 0 1 1 0 00-1.414 1.414 4 4 0 005.656 0l3-3a4 4 0 00-5.656-5.656l-1.5 1.5a1 1 0 101.414 1.414l1.5-1.5zm-5 5a2 2 0 012.828 0 1 1 0 101.414-1.414 4 4 0 00-5.656 0l-3 3a4 4 0 105.656 5.656l1.5-1.5a1 1 0 10-1.414-1.414l-1.5 1.5a2 2 0 11-2.828-2.828l3-3z"
              clipRule="evenodd"
            />
          </svg>
        </div>
        <p className="text-sm font-medium text-slate-300 group-hover:text-white truncate" title={source.web.title}>
          {source.web.title}
        </p>
      </div>
    </a>
  );
};

export default SourceCard;
