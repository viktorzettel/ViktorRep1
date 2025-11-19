
import React, { useEffect, useRef, useState, useCallback } from 'react';
import { 
  SUN_RADIUS_KM, 
  TRAVEL_SPEED_KM_S, 
  SPEED_OF_LIGHT_KM_S,
  SUN_VIEWPORT_RATIO, 
  INITIAL_CAMERA_X_KM,
  AU_IN_KM,
  PLANETS,
  ASTEROID_BELT_START_KM,
  ASTEROID_BELT_END_KM
} from '../constants';
import { useKeyboard } from '../hooks/useKeyboard';

// Helper to convert hex to rgba for gradients
const hexToRgba = (hex: string, alpha: number) => {
  const cleanHex = hex.replace('#', '');
  let r = 0, g = 0, b = 0;
  
  if (cleanHex.length === 3) {
    r = parseInt(cleanHex[0] + cleanHex[0], 16);
    g = parseInt(cleanHex[1] + cleanHex[1], 16);
    b = parseInt(cleanHex[2] + cleanHex[2], 16);
  } else if (cleanHex.length === 6) {
    r = parseInt(cleanHex.substring(0, 2), 16);
    g = parseInt(cleanHex.substring(2, 4), 16);
    b = parseInt(cleanHex.substring(4, 6), 16);
  }
  
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
};

// Constants for Ruler
const RULER_TICK_KM = 100000; // 100,000 km
const RULER_MAJOR_TICK_KM = 1000000; // 1,000,000 km

export const SpaceViewport: React.FC = () => {
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  
  // Track loading status of the background image.  This is not displayed to the user but
  // helps us determine when to fade in the Milky Way panorama or fall back to black.
  const [bgStatus, setBgStatus] = useState<'UNKNOWN' | 'LOADED' | 'ERROR'>('UNKNOWN');

  // We store camera position in a ref for the animation loop to avoid React state lag,
  // but we also sync it to state for rendering updates.
  const cameraXRef = useRef(INITIAL_CAMERA_X_KM);
  const [cameraX, setCameraX] = useState(INITIAL_CAMERA_X_KM);

  // Auto-travel state (Light-bulb mode)
  const [isAutoTravel, setIsAutoTravel] = useState(false);
  const isAutoTravelRef = useRef(false);
  
  const activeKeys = useKeyboard();
  const requestRef = useRef<number | undefined>(undefined);
  const previousTimeRef = useRef<number | undefined>(undefined);

  // 1. Handle Window Resizing
  useEffect(() => {
    const handleResize = () => {
      setDimensions({
        width: window.innerWidth,
        height: window.innerHeight,
      });
    };
    
    handleResize(); // Initial measurement
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // 2. Physics Engine (Game Loop)
  const animate = useCallback((time: number) => {
    if (previousTimeRef.current !== undefined) {
      const deltaTimeSeconds = (time - previousTimeRef.current) / 1000;
      
      let moveDelta = 0;

      if (isAutoTravelRef.current) {
        // Auto mode: 1x Speed of Light, forward only
        moveDelta = SPEED_OF_LIGHT_KM_S * deltaTimeSeconds;
      } else {
        // Manual mode: 5x Speed of Light via Keys
        // Right arrow: Move away from Sun (+x)
        if (activeKeys.current.has('ArrowRight')) {
          moveDelta += TRAVEL_SPEED_KM_S * deltaTimeSeconds;
        }
        // Left arrow: Move towards Sun (-x)
        if (activeKeys.current.has('ArrowLeft')) {
          moveDelta -= TRAVEL_SPEED_KM_S * deltaTimeSeconds;
        }
      }

      if (moveDelta !== 0) {
        cameraXRef.current += moveDelta;
        
        // Prevent moving into negative distance (past Sun center)
        if (cameraXRef.current < 0) cameraXRef.current = 0;

        setCameraX(cameraXRef.current);
      }
    }
    previousTimeRef.current = time;
    requestRef.current = requestAnimationFrame(animate);
  }, [activeKeys]);

  useEffect(() => {
    requestRef.current = requestAnimationFrame(animate);
    return () => {
      if (requestRef.current) cancelAnimationFrame(requestRef.current);
    };
  }, [animate]);

  // Teleport Handler
  const handleTeleport = (targetKm: number) => {
    // Stop auto travel when manually teleporting
    setIsAutoTravel(false);
    isAutoTravelRef.current = false;

    cameraXRef.current = targetKm;
    setCameraX(targetKm);
    
    // Blur the button so that subsequent arrow key presses don't interact with the UI
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
  };

  // Toggle Auto Travel
  const toggleAutoTravel = () => {
    const newState = !isAutoTravel;
    setIsAutoTravel(newState);
    isAutoTravelRef.current = newState;
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
  };

  // 3. Calculate Scales & Positions
  // Scale factor: How many Pixels represent 1 Kilometer?
  const sunDiameterKm = SUN_RADIUS_KM * 2;
  // Prevent division by zero or negative height during init
  const safeHeight = dimensions.height || 800; 
  const pixelsPerKm = (safeHeight * SUN_VIEWPORT_RATIO) / sunDiameterKm;

  // Screen Center X coordinate
  const screenCenterX = dimensions.width / 2;

  // Sun Render Props
  const sunPixelRadius = SUN_RADIUS_KM * pixelsPerKm;
  const sunScreenCenterX = screenCenterX - (cameraX * pixelsPerKm);

  // Helper to get screen X for any km distance
  const getScreenX = (distanceKm: number) => {
    return screenCenterX + (distanceKm - cameraX) * pixelsPerKm;
  };

  return (
    <div className="relative w-full h-full bg-black isolate overflow-hidden">
      
      {/*
        Background and overlay
        We no longer display a debug label or gradient.  The Milky Way image will load from the
        public folder and fill the viewport.  In case it fails to load, the image element will
        disappear and the black background of the container will show through.
      */}

      {/*
        BACKGROUND LAYER
        Load the high-quality Milky Way panorama from the public folder.  If the image cannot be
        loaded for any reason, the <img> element will be hidden and the underlying black
        background will remain visible.  An overlay darkens the image slightly to improve
        contrast for on-screen text and UI elements.
      */}
      <div className="absolute inset-0 z-0 select-none pointer-events-none">
        <img
          src="/milkyway_bg.jpg"
          alt="Milky Way background"
          className={`absolute inset-0 w-full h-full object-cover transition-opacity duration-700 ${bgStatus === 'LOADED' ? 'opacity-100' : 'opacity-0'}`}
          onLoad={() => {
            // Mark the background as successfully loaded so that it fades in smoothly.
            setBgStatus('LOADED');
          }}
          onError={(e) => {
            // If the image fails to load, hide the element entirely so the black background shows.
            setBgStatus('ERROR');
            e.currentTarget.style.display = 'none';
          }}
        />
        {/*
          Apply a semi-transparent black overlay to the entire viewport.  This helps keep
          planetary labels and UI elements readable on top of the detailed starfield.
        */}
        <div className="absolute inset-0 bg-black/30" />
      </div>

      {/* Navigation Toolbar */}
      <div className="absolute top-0 w-full flex justify-center items-start p-4 z-[60] pointer-events-none">
        <div className="pointer-events-auto bg-gray-900/90 backdrop-blur-md border border-white/20 rounded-full px-4 py-2 flex flex-wrap justify-center gap-2 shadow-2xl">
           <button 
             onClick={() => handleTeleport(INITIAL_CAMERA_X_KM)}
             className="px-3 py-1 text-xs md:text-sm font-medium text-yellow-400 hover:bg-white/10 rounded transition-colors"
           >
             Sun
           </button>
           
           {/* Inner Planets */}
           {PLANETS.slice(0, 4).map(p => (
             <button
               key={p.name}
               onClick={() => handleTeleport(p.distanceFromSunKm)}
               className="px-3 py-1 text-xs md:text-sm font-medium text-gray-200 hover:bg-white/10 rounded transition-colors"
             >
               {p.name}
             </button>
           ))}

           {/* Asteroid Belt */}
           <button 
             onClick={() => handleTeleport((ASTEROID_BELT_START_KM + ASTEROID_BELT_END_KM) / 2)}
             className="px-3 py-1 text-xs md:text-sm font-medium text-stone-400 hover:bg-white/10 rounded transition-colors"
           >
             Belt
           </button>

           {/* Outer Planets */}
           {PLANETS.slice(4).map(p => (
             <button
               key={p.name}
               onClick={() => handleTeleport(p.distanceFromSunKm)}
               className="px-3 py-1 text-xs md:text-sm font-medium text-gray-200 hover:bg-white/10 rounded transition-colors"
             >
               {p.name}
             </button>
           ))}
        </div>
      </div>

      {/* 
        Sun Render 
      */}
      <div 
        className="absolute rounded-full bg-gradient-to-r from-yellow-200 via-yellow-500 to-orange-600 shadow-[0_0_100px_rgba(255,160,0,0.6)]"
        style={{
          width: `${sunPixelRadius * 2}px`,
          height: `${sunPixelRadius * 2}px`,
          left: `${sunScreenCenterX - sunPixelRadius}px`,
          top: `50%`,
          transform: `translateY(-50%)`,
          zIndex: 10,
        }}
      />

      {/* 
        Asteroid Belt Render
        Rendered as a semi-transparent band
      */}
      {(() => {
        const startX = getScreenX(ASTEROID_BELT_START_KM);
        const endX = getScreenX(ASTEROID_BELT_END_KM);
        const width = endX - startX;
        // Only render if it has positive width and is potentially visible (loose cull)
        if (width > 0) {
          return (
            <React.Fragment>
              {/* Belt Label */}
              <div 
                className="absolute text-white/40 text-xs uppercase tracking-widest text-center font-medium"
                style={{
                  left: `${startX + width/2}px`,
                  top: '50%',
                  transform: 'translate(-50%, -180px)', // Position above the band
                  zIndex: 5,
                  textShadow: '0 1px 3px rgba(0,0,0,0.8)'
                }}
              >
                Asteroid Belt
              </div>
              {/* Belt Graphic */}
              <div 
                className="absolute bg-white/10 border-y border-white/5"
                style={{
                  left: `${startX}px`,
                  width: `${width}px`,
                  top: '50%',
                  height: '300px', // Arbitrary "zone" height
                  transform: 'translateY(-50%)',
                  zIndex: 1,
                }}
              />
            </React.Fragment>
          );
        }
        return null;
      })()}

      {/* 
        Planets & Moons Render 
      */}
      {PLANETS.map((planet) => {
        const planetScreenX = getScreenX(planet.distanceFromSunKm);
        const planetPixelRadius = Math.max(planet.radiusKm * pixelsPerKm, 1); // Minimum 1px size
        const diameter = planetPixelRadius * 2;

        // Simple culling
        if (planetScreenX + diameter < -dimensions.width || planetScreenX - diameter > dimensions.width * 2) {
           // Off screen
           return null;
        }

        return (
          <React.Fragment key={planet.name}>
            {/* Planet Label */}
            <div 
              className="absolute text-white font-medium text-sm whitespace-nowrap pointer-events-none"
              style={{
                left: `${planetScreenX}px`,
                top: '50%',
                transform: `translate(-50%, calc(-50% - ${planetPixelRadius + 20}px))`, // 20px above planet
                zIndex: 30,
                textShadow: '0 2px 4px rgba(0,0,0,0.9)' // Added shadow for stars background
              }}
            >
              {planet.name}
            </div>

            {/* 
              Planet Rings (Saturn, Uranus) 
              Improved 3D-like rendering with front/back segments
            */}
            {planet.rings && (() => {
              const outerRadiusPx = planet.rings.outerRadiusKm * pixelsPerKm;
              const innerRadiusPx = planet.rings.innerRadiusKm * pixelsPerKm;
              const width = outerRadiusPx * 2;
              
              // Tilt ratio (0.35 creates a nice elliptical perspective)
              const tiltRatio = 0.35; 
              const height = width * tiltRatio;
              
              const innerPercent = (innerRadiusPx / outerRadiusPx) * 100;
              
              const ringRgba = hexToRgba(planet.rings.color, planet.rings.opacity);
              
              // Using radial-gradient to create the ring with a hole
              // ellipse closest-side fits the gradient to the box shape (ellipse)
              const gradient = `radial-gradient(ellipse closest-side at center, transparent ${innerPercent}%, ${ringRgba} ${innerPercent}%, ${ringRgba} 100%, transparent 100%)`;

              const rotation = planet.rings.rotationDeg || 0;

              return (
                <>
                  {/* Back Ring Segment (Top half of ellipse) - Behind Planet */}
                  <div 
                    className="absolute"
                    style={{
                      left: `${planetScreenX - outerRadiusPx}px`,
                      top: '50%',
                      width: `${width}px`,
                      height: `${height}px`,
                      background: gradient,
                      transform: `translateY(-50%) rotate(${rotation}deg)`,
                      zIndex: 15, // Behind planet (which is 20)
                      clipPath: 'polygon(0 0, 100% 0, 100% 50%, 0 50%)'
                    }}
                  />
                  
                  {/* Front Ring Segment (Bottom half of ellipse) - In front of Planet */}
                  <div 
                    className="absolute"
                    style={{
                      left: `${planetScreenX - outerRadiusPx}px`,
                      top: '50%',
                      width: `${width}px`,
                      height: `${height}px`,
                      background: gradient,
                      transform: `translateY(-50%) rotate(${rotation}deg)`,
                      zIndex: 25, // In front of planet (which is 20)
                      clipPath: 'polygon(0 50%, 100% 50%, 100% 100%, 0 100%)'
                    }}
                  />
                </>
              );
            })()}

            {/* Planet Body */}
            <div
              className="absolute rounded-full"
              style={{
                // For the inner rocky planets we apply a subtle radial gradient to give them more visual interest.
                background: (() => {
                  // Determine if this is one of the first four planets (Mercury, Venus, Earth, Mars)
                  const innerIndex = PLANETS.findIndex(p => p.name === planet.name);
                  if (innerIndex >= 0 && innerIndex < 4) {
                    // Use the planet's base color and create a radial gradient that fades towards the edges.
                    const color = planet.color;
                    const mid = hexToRgba(color, 0.8);
                    const edge = hexToRgba(color, 1);
                    return `radial-gradient(circle at 30% 30%, ${color} 0%, ${mid} 70%, ${edge} 100%)`;
                  }
                  // For outer planets and gas giants, stick with a solid fill.
                  return planet.color;
                })(),
                width: `${diameter}px`,
                height: `${diameter}px`,
                left: `${planetScreenX - planetPixelRadius}px`,
                top: '50%',
                transform: 'translateY(-50%)',
                zIndex: 20,
                boxShadow: '0 0 5px rgba(0,0,0,0.5)' // Shadow for definition against stars
              }}
            />

            {/* Moons Render */}
            {planet.moons?.map(moon => {
               // We place moons at (PlanetX + MoonDistance) along the same axis for 1D sim
               const moonScreenX = getScreenX(planet.distanceFromSunKm + moon.distanceFromPlanetKm);
               const moonPixelRadius = Math.max(moon.radiusKm * pixelsPerKm, 0.5); // Min 0.5px for faint visibility
               const moonDiameter = moonPixelRadius * 2;
               
               const shouldShowLabel = ['Moon', 'Io', 'Europa', 'Ganymede', 'Callisto', 'Titan', 'Rhea'].includes(moon.name);

               return (
                 <React.Fragment key={moon.name}>
                    {shouldShowLabel && (
                      <div 
                        className="absolute text-gray-300 text-xs whitespace-nowrap pointer-events-none"
                        style={{
                          left: `${moonScreenX}px`,
                          top: '50%',
                          transform: `translate(-50%, calc(-50% - ${moonPixelRadius + 12}px))`, // Slightly closer than planet labels
                          zIndex: 25,
                          opacity: 0.9,
                          textShadow: '0 1px 2px rgba(0,0,0,1)' // Added shadow for stars background
                        }}
                      >
                        {moon.name}
                      </div>
                    )}
                    <div
                        className="absolute rounded-full"
                        style={{
                        backgroundColor: moon.color,
                        width: `${moonDiameter}px`,
                        height: `${moonDiameter}px`,
                        left: `${moonScreenX - moonPixelRadius}px`,
                        top: '50%',
                        transform: 'translateY(-50%)',
                        zIndex: 21,
                        }}
                    />
                 </React.Fragment>
               );
            })}
          </React.Fragment>
        );
      })}

      {/* 
        Ruler Overlay (Minimalist Distance Scale)
      */}
      <div className="absolute bottom-0 left-0 w-full h-16 pointer-events-none z-40 select-none overflow-hidden">
        {/* Baseline */}
        <div className="absolute bottom-0 w-full border-b border-white/20"></div>
        
        {(() => {
           // Don't render if invalid scale
           if (pixelsPerKm <= 0) return null;

           // Calculate visible km range
           const visibleHalfWidthKm = (dimensions.width / 2) / pixelsPerKm;
           const startKm = Math.floor((cameraX - visibleHalfWidthKm) / RULER_TICK_KM) * RULER_TICK_KM;
           const endKm = Math.ceil((cameraX + visibleHalfWidthKm) / RULER_TICK_KM) * RULER_TICK_KM;
           
           // Use index loop to avoid float accumulation errors
           const startIdx = Math.floor(startKm / RULER_TICK_KM);
           const endIdx = Math.ceil(endKm / RULER_TICK_KM);

           const ticks = [];
           
           // Prevent rendering too many ticks if zoomed abnormally (safety cap)
           if (endIdx - startIdx > 500) return null;

           for (let i = startIdx; i <= endIdx; i++) {
              const k = i * RULER_TICK_KM;
              const isMajor = Math.abs(k % RULER_MAJOR_TICK_KM) < (RULER_TICK_KM / 2);
              const screenX = getScreenX(k);
              
              // Cull off-screen ticks slightly loosely
              if (screenX < -50 || screenX > dimensions.width + 50) continue;

              ticks.push(
                <div 
                  key={k} 
                  className={`absolute bottom-0 border-l border-white/50 transform -translate-x-1/2 transition-none`}
                  style={{
                    left: `${screenX}px`,
                    height: isMajor ? '24px' : '10px',
                    borderLeftWidth: isMajor ? '2px' : '1px',
                    opacity: isMajor ? 0.8 : 0.3
                  }}
                >
                  {isMajor && (
                     <div className="absolute bottom-7 left-1/2 -translate-x-1/2 text-[10px] text-white/70 font-mono whitespace-nowrap drop-shadow-md">
                       {/* Format: 1M, 2M, etc. */}
                       {(k / 1000000).toLocaleString()}M
                     </div>
                  )}
                </div>
              );
           }
           return ticks;
        })()}
      </div>

      {/* Debug Readout - Bottom Left */}
      <div className="absolute bottom-4 left-4 font-mono text-green-400 bg-black/80 p-4 rounded border border-green-900/50 pointer-events-none select-none z-50 backdrop-blur-sm">
        <div className="text-xs text-gray-400 mb-1 uppercase tracking-wider">Distance from Sun</div>
        <div className="text-lg">{Math.floor(cameraX).toLocaleString()} km</div>
        <div className="text-sm text-green-600">{(cameraX / AU_IN_KM).toFixed(6)} AU</div>
        {isAutoTravel && <div className="text-xs text-yellow-400 mt-2 animate-pulse">AUTO-PILOT ACTIVE (1c)</div>}
      </div>

      {/* Light-bulb Toggle Button - Bottom Right */}
      <button
        onClick={toggleAutoTravel}
        className={`absolute bottom-4 right-4 p-3 rounded-full transition-all duration-300 border z-50 outline-none focus:outline-none focus:ring-2 focus:ring-yellow-500 ${
          isAutoTravel 
            ? 'bg-yellow-500/20 border-yellow-400 text-yellow-400 shadow-[0_0_15px_rgba(250,204,21,0.5)]' 
            : 'bg-gray-800/80 border-white/10 text-gray-400 hover:bg-white/10 hover:text-white backdrop-blur-md'
        }`}
        title={isAutoTravel ? "Stop Light-Speed Travel" : "Engage Auto-Pilot (1x Speed of Light)"}
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-1 1.5-2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5" />
          <path d="M9 18h6" />
          <path d="M10 22h4" />
        </svg>
      </button>

    </div>
  );
};
