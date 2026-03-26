import React from 'react';
import { SpaceViewport } from './components/SpaceViewport';

const App: React.FC = () => {
  return (
    <div className="w-screen h-screen bg-black text-white overflow-hidden">
      <SpaceViewport />
    </div>
  );
};

export default App;