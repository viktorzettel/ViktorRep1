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
  ASTEROID_BELT_END_KM,
  LIGHT_YEAR_IN_KM
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

// Helper to format numbers (German locale for . thousands separator and , decimal)
const formatNumber = (num: number, maxDecimals: number = 0) => {
  return num.toLocaleString('de-DE', { maximumFractionDigits: maxDecimals, minimumFractionDigits: 0 });
};

export const SpaceViewport: React.FC = () => {
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });

  // Track loading status of the background image.
  const [bgStatus, setBgStatus] = useState<'UNKNOWN' | 'LOADED' | 'ERROR'>('UNKNOWN');

  // Camera position state
  const cameraXRef = useRef(INITIAL_CAMERA_X_KM);
  const [cameraX, setCameraX] = useState(INITIAL_CAMERA_X_KM);

  // Auto-travel state
  const [autoTravelMode, setAutoTravelMode] = useState<'light' | 'rocket' | null>(null); // 'light' or 'rocket' or null
  const autoTravelModeRef = useRef<'light' | 'rocket' | null>(null);

  // Physics constants
  // Physics constants
  const STARSHIP_SPEED_KM_S = 30; // Approx 108,000 km/h (30 km/s)

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

  // Sync autoTravelMode state with ref for animation loop
  useEffect(() => {
    autoTravelModeRef.current = autoTravelMode;
  }, [autoTravelMode]);

  // 2. Physics Engine (Game Loop)
  const animate = useCallback((time: number) => {
    if (previousTimeRef.current !== undefined) {
      const deltaTimeSeconds = (time - previousTimeRef.current) / 1000;

      let moveDelta = 0;

      if (autoTravelModeRef.current) {
        const speed = autoTravelModeRef.current === 'light' ? SPEED_OF_LIGHT_KM_S : STARSHIP_SPEED_KM_S;
        moveDelta = speed * deltaTimeSeconds;
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
    setAutoTravelMode(null);

    cameraXRef.current = targetKm;
    setCameraX(targetKm);

    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
  };

  // Toggle Auto Travel
  const toggleLightTravel = () => {
    setAutoTravelMode((prev) => (prev === 'light' ? null : 'light'));
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
  };

  const toggleRocketTravel = () => {
    setAutoTravelMode((prev) => (prev === 'rocket' ? null : 'rocket'));
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
  };

  // 3. Calculate Scales & Positions
  const sunDiameterKm = SUN_RADIUS_KM * 2;
  const safeHeight = dimensions.height || 800;
  const pixelsPerKm = (safeHeight * SUN_VIEWPORT_RATIO) / sunDiameterKm;

  const screenCenterX = dimensions.width / 2;
  const sunPixelRadius = SUN_RADIUS_KM * pixelsPerKm;
  const sunScreenCenterX = screenCenterX - (cameraX * pixelsPerKm);

  // Helper to get screen X for any km distance
  const getScreenX = (distanceKm: number) => {
    return screenCenterX + (distanceKm - cameraX) * pixelsPerKm;
  };

  return (
    <div className="relative w-full h-full bg-black isolate overflow-hidden">

      {/* Background Layer */}
      <div className="absolute inset-0 z-0 select-none pointer-events-none">
        <img
          src="/milkyway_bg.jpg"
          alt="Milky Way background"
          className={`absolute inset-0 w-full h-full object-cover transition-opacity duration-700 ${bgStatus === 'LOADED' ? 'opacity-100' : 'opacity-0'}`}
          style={{ transform: 'scale(1.5) translateY(-15%)' }}
          onLoad={() => setBgStatus('LOADED')}
          onError={(e) => {
            setBgStatus('ERROR');
            e.currentTarget.style.display = 'none';
          }}
        />
        <div className="absolute inset-0 bg-black/30" />
      </div>

      {/* Navigation Toolbar */}
      <div className="absolute top-6 w-full flex justify-center items-start z-[60] pointer-events-none">
        <div className="pointer-events-auto bg-white/10 backdrop-blur-xl border border-white/20 rounded-full px-2 py-2 flex flex-wrap justify-center gap-1 shadow-[0_8px_32px_0_rgba(0,0,0,0.37)] transition-all hover:bg-white/15 hover:border-white/30 hover:shadow-[0_8px_32px_0_rgba(255,255,255,0.1)]">
          <button
            onClick={() => handleTeleport(INITIAL_CAMERA_X_KM)}
            className="px-4 py-2 text-xs md:text-sm font-medium text-white/90 hover:bg-white/20 rounded-full transition-all duration-300 hover:scale-105 active:scale-95"
          >
            Sun
          </button>

          {/* Inner Planets */}
          {PLANETS.slice(0, 4).map(p => (
            <button
              key={p.name}
              onClick={() => handleTeleport(p.distanceFromSunKm)}
              className="px-4 py-2 text-xs md:text-sm font-medium text-white/80 hover:text-white hover:bg-white/20 rounded-full transition-all duration-300 hover:scale-105 active:scale-95"
            >
              {p.name}
            </button>
          ))}

          {/* Asteroid Belt */}
          <button
            onClick={() => handleTeleport((ASTEROID_BELT_START_KM + ASTEROID_BELT_END_KM) / 2)}
            className="px-4 py-2 text-xs md:text-sm font-medium text-white/60 hover:text-white hover:bg-white/20 rounded-full transition-all duration-300 hover:scale-105 active:scale-95"
          >
            Belt
          </button>

          {/* Outer Planets */}
          {PLANETS.slice(4).map(p => (
            <button
              key={p.name}
              onClick={() => handleTeleport(p.distanceFromSunKm)}
              className="px-4 py-2 text-xs md:text-sm font-medium text-white/80 hover:text-white hover:bg-white/20 rounded-full transition-all duration-300 hover:scale-105 active:scale-95"
            >
              {p.name}
            </button>
          ))}
        </div>
      </div>

      {/* Sun Render */}
      <div
        className="absolute rounded-full"
        style={{
          width: `${sunPixelRadius * 2}px`,
          height: `${sunPixelRadius * 2}px`,
          left: `${sunScreenCenterX - sunPixelRadius}px`,
          top: `50%`,
          transform: `translateY(-50%)`,
          zIndex: 10,
          background: `
            radial-gradient(circle at 75% 40%, rgba(100,0,0,0.2) 0%, transparent 8%),
            radial-gradient(circle at 25% 65%, rgba(100,0,0,0.15) 0%, transparent 6%),
            radial-gradient(circle at 60% 20%, rgba(255,255,255,0.1) 0%, transparent 20%),
            radial-gradient(circle at 30% 30%, rgba(255,255,255,0.9) 0%, rgba(255,240,200,0.2) 30%, transparent 60%),
            radial-gradient(circle at 50% 50%, #FFF176 0%, #F57F17 40%, #E65100 70%, #BF360C 100%)
          `,
          boxShadow: `
            0 0 60px rgba(255, 160, 0, 0.8),
            0 0 120px rgba(255, 69, 0, 0.6),
            0 0 200px rgba(255, 0, 0, 0.4),
            inset 0 0 40px rgba(0,0,0,0.2)
          `
        }}
      />

      {/* Asteroid Belt Render */}
      {(() => {
        const centerKm = (ASTEROID_BELT_START_KM + ASTEROID_BELT_END_KM) / 2;
        const screenX = getScreenX(centerKm);

        // Only render if roughly on screen
        if (screenX < -500 || screenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${screenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <h1 className="text-2xl font-bold mb-2 bg-gradient-to-r from-gray-300 to-stone-400 bg-clip-text text-transparent">
                Asteroid Belt
              </h1>
              <p className="text-lg font-light text-white/80 leading-relaxed">
                A vast doughnut-shaped ring between Mars and Jupiter, containing millions, if not billions, of rocky remnants from the solar system's formation.
              </p>
            </div>
          </div>
        );
      })()}

      {/* Planets & Moons Render */}
      {PLANETS.map((planet) => {
        const planetScreenX = getScreenX(planet.distanceFromSunKm);
        const planetPixelRadius = Math.max(planet.radiusKm * pixelsPerKm, 1);
        const diameter = planetPixelRadius * 2;

        if (planetScreenX + diameter < -dimensions.width || planetScreenX - diameter > dimensions.width * 2) {
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
                transform: `translate(-50%, calc(-50% - ${planetPixelRadius + 20}px))`,
                zIndex: 30,
                textShadow: '0 2px 4px rgba(0,0,0,0.9)'
              }}
            >
              {planet.name}
            </div>

            {/* Planet Rings */}
            {planet.rings && (() => {
              const outerRadiusPx = planet.rings.outerRadiusKm * pixelsPerKm;
              const innerRadiusPx = planet.rings.innerRadiusKm * pixelsPerKm;
              const width = outerRadiusPx * 2;
              const tiltRatio = 0.35;
              const height = width * tiltRatio;
              const innerPercent = (innerRadiusPx / outerRadiusPx) * 100;
              const ringRgba = hexToRgba(planet.rings.color, planet.rings.opacity);

              let gradient;
              if (planet.name === 'Saturn') {
                // Realistic Saturn Rings: C Ring (faint), B Ring (bright), Cassini Division (gap), A Ring (outer)
                // We map the physical structure to percentages of the ring width
                // Inner edge is ~74,500km, Outer edge is ~140,220km.
                // This gradient runs from center (0%) to edge (100%).
                // We need to start at innerPercent.

                gradient = `radial-gradient(ellipse closest-side at center, 
                   transparent ${innerPercent}%, 
                   rgba(100, 90, 70, 0.3) ${innerPercent}%,     /* C Ring Start */
                   rgba(100, 90, 70, 0.4) ${innerPercent + 10}%, /* C Ring End / B Ring Start */
                   rgba(210, 190, 150, 0.9) ${innerPercent + 12}%, /* B Ring (Brightest) */
                   rgba(180, 160, 120, 0.8) 85%,                 /* B Ring Outer Edge */
                   rgba(0, 0, 0, 0.8) 86%,                       /* Cassini Division (Dark Gap) */
                   rgba(0, 0, 0, 0.8) 88%, 
                   rgba(160, 150, 130, 0.7) 89%,                 /* A Ring Start */
                   rgba(150, 140, 120, 0.6) 98%,                 /* A Ring End */
                   transparent 100%
                 )`;
              } else {
                // Standard rings for other planets
                gradient = `radial-gradient(ellipse closest-side at center, transparent ${innerPercent}%, ${ringRgba} ${innerPercent}%, ${ringRgba} 100%, transparent 100%)`;
              }

              const rotation = planet.rings.rotationDeg || 0;

              return (
                <>
                  <div
                    className="absolute"
                    style={{
                      left: `${planetScreenX - outerRadiusPx}px`,
                      top: '50%',
                      width: `${width}px`,
                      height: `${height}px`,
                      background: gradient,
                      transform: `translateY(-50%) rotate(${rotation}deg)`,
                      zIndex: 15,
                      clipPath: 'polygon(0 0, 100% 0, 100% 50%, 0 50%)'
                    }}
                  />
                  <div
                    className="absolute"
                    style={{
                      left: `${planetScreenX - outerRadiusPx}px`,
                      top: '50%',
                      width: `${width}px`,
                      height: `${height}px`,
                      background: gradient,
                      transform: `translateY(-50%) rotate(${rotation}deg)`,
                      zIndex: 25,
                      clipPath: 'polygon(0 50%, 100% 50%, 100% 100%, 0 100%)'
                    }}
                  />
                </>
              );
            })()}

            {/* Planet Body - UPDATED TEXTURES */}
            <div
              className="absolute rounded-full"
              style={{
                background: (() => {
                  // --- NEW: Realistic Inner Planet Textures ---

                  // Mercury: Stark, cratered, uneven lighting (Grey/Silver)
                  if (planet.name === 'Mercury') {
                    return 'radial-gradient(circle at 30% 30%, #E0E0E0 0%, #9E9E9E 40%, #616161 70%, #212121 100%)';
                  }

                  // Venus: Thick, hazy, yellowish atmosphere (Cream/Gold)
                  if (planet.name === 'Venus') {
                    return 'radial-gradient(circle at 35% 35%, #FFF8E1 0%, #F5DEB3 40%, #D2B48C 70%, #8B4513 100%)';
                  }

                  // Earth: Blue marble with green/brown continents and clouds
                  if (planet.name === 'Earth') {
                    // 1. Specular highlight (Sun reflection)
                    const reflection = 'radial-gradient(circle at 25% 25%, rgba(255,255,255,0.5) 0%, transparent 20%)';

                    // 2. Clouds (White semi-transparent blobs)
                    const clouds1 = 'radial-gradient(ellipse at 75% 25%, rgba(255,255,255,0.4) 0%, transparent 25%)';
                    const clouds2 = 'radial-gradient(ellipse at 25% 75%, rgba(255,255,255,0.3) 0%, transparent 20%)';
                    const clouds3 = 'radial-gradient(ellipse at 50% 50%, rgba(255,255,255,0.1) 0%, transparent 50%)';

                    // 3. Continents (Green/Brown)
                    // North America / Eurasia ish
                    const land1 = 'radial-gradient(ellipse at 60% 40%, #4CAF50 0%, #2E7D32 40%, transparent 60%)';
                    // Africa / Desert ish
                    const land2 = 'radial-gradient(ellipse at 40% 60%, #8D6E63 0%, #5D4037 40%, transparent 50%)';
                    // South America / Forest ish
                    const land3 = 'radial-gradient(ellipse at 70% 70%, #388E3C 0%, transparent 30%)';

                    // 4. Base Ocean (Deep Blue with atmospheric edge)
                    const ocean = 'radial-gradient(circle at 40% 40%, #2196F3 0%, #1565C0 50%, #0D47A1 80%, #000000 100%)';

                    return `${reflection}, ${clouds1}, ${clouds2}, ${clouds3}, ${land1}, ${land2}, ${land3}, ${ocean}`;
                  }

                  // Mars: Rusty, dusty red surface with terminator shading
                  if (planet.name === 'Mars') {
                    return 'radial-gradient(circle at 35% 30%, #FF8A65 0%, #D84315 45%, #8D280B 80%, #3E1105 100%)';
                  }

                  // --- Outer Planets (Refined Logic) ---

                  // Jupiter: Complex banding + Great Red Spot
                  if (planet.name === 'Jupiter') {
                    // Lighting/Shadow overlay
                    const lighting = 'radial-gradient(circle at 30% 30%, rgba(255,255,255,0.4) 0%, rgba(0,0,0,0) 50%, rgba(0,0,0,0.6) 100%)';
                    // Great Red Spot
                    const redSpot = 'radial-gradient(ellipse at 70% 60%, rgba(165, 42, 42, 0.8) 0%, transparent 12%)';
                    // Detailed Bands
                    const bands = `linear-gradient(180deg, 
                      #5C4A3D 0%,   /* N. Polar */
                      #8B7355 10%, 
                      #D6C2AD 20%,  /* N. Temperate Zone */
                      #8F7661 30%,  /* N. Equatorial Belt */
                      #EED5A5 45%,  /* Equatorial Zone */
                      #8F7661 55%,  /* S. Equatorial Belt */
                      #D6C2AD 70%,  /* S. Temperate Zone */
                      #8B7355 85%, 
                      #5C4A3D 100%  /* S. Polar */
                    )`;
                    return `${lighting}, ${redSpot}, ${bands}`;
                  }

                  // Saturn: Soft gold/beige banding
                  if (planet.name === 'Saturn') {
                    const lighting = 'radial-gradient(circle at 30% 30%, rgba(255,255,255,0.4) 0%, rgba(0,0,0,0) 50%, rgba(0,0,0,0.6) 100%)';
                    // More realistic banding with subtle color shifts
                    const bands = `linear-gradient(180deg, 
                      #A49B72 0%,   /* N. Polar Hexagon area */
                      #C5B488 15%,  /* N. High Latitudes */
                      #E3D0A6 30%,  /* N. Temperate */
                      #EEDCB2 45%,  /* Equatorial Zone (Bright) */
                      #E3D0A6 60%,  /* S. Temperate */
                      #C5B488 80%,  /* S. High Latitudes */
                      #A49B72 100%  /* S. Polar */
                    )`;
                    return `${lighting}, ${bands}`;
                  }

                  // Uranus: Featureless pale cyan with atmospheric depth
                  if (planet.name === 'Uranus') {
                    return 'radial-gradient(circle at 30% 30%, #E0FFFF 0%, #AFEEEE 40%, #7FFFD4 70%, #008B8B 100%)';
                  }

                  // Neptune: Deep azure blue with Dark Spot and subtle banding
                  if (planet.name === 'Neptune') {
                    const lighting = 'radial-gradient(circle at 30% 30%, rgba(255,255,255,0.4) 0%, transparent 60%, rgba(0,0,0,0.7) 100%)';
                    // Great Dark Spot
                    const darkSpot = 'radial-gradient(ellipse at 75% 45%, rgba(0,0,60, 0.6) 0%, transparent 18%)';
                    // Subtle cloud streaks (Scooters)
                    const clouds = 'radial-gradient(ellipse at 30% 60%, rgba(255,255,255,0.2) 0%, transparent 10%)';
                    // Atmospheric banding
                    const bands = `linear-gradient(180deg, 
                      #3a5fcd 0%, 
                      #4169E1 20%, 
                      #4682B4 40%, 
                      #5B5DDF 50%, 
                      #4169E1 60%, 
                      #3a5fcd 80%, 
                      #27408B 100%
                    )`;
                    return `${lighting}, ${darkSpot}, ${clouds}, ${bands}`;
                  }

                  // Fallback for anything else
                  return planet.color;
                })(),
                width: `${diameter}px`,
                height: `${diameter}px`,
                left: `${planetScreenX - planetPixelRadius}px`,
                top: '50%',
                transform: 'translateY(-50%)',
                zIndex: 20,
                // Added dynamic box-shadow for atmosphere glow on Earth/Venus
                boxShadow: (() => {
                  if (planet.name === 'Earth') return '0 0 8px rgba(79, 151, 207, 0.6)';
                  if (planet.name === 'Venus') return '0 0 8px rgba(255, 248, 225, 0.4)';
                  return '0 0 5px rgba(0,0,0,0.5)';
                })()
              }}
            />

            {/* Moons Render */}
            {planet.moons?.map(moon => {
              const moonScreenX = getScreenX(planet.distanceFromSunKm + moon.distanceFromPlanetKm);
              const moonPixelRadius = Math.max(moon.radiusKm * pixelsPerKm, 0.5);
              const moonDiameter = moonPixelRadius * 2;

              const shouldShowLabel = ['Moon', 'Io', 'Europa', 'Ganymede', 'Callisto', 'Titan', 'Rhea', 'Miranda', 'Ariel', 'Umbriel', 'Titania', 'Oberon', 'Triton'].includes(moon.name);

              return (
                <React.Fragment key={moon.name}>
                  {shouldShowLabel && (
                    <div
                      className="absolute text-gray-300 text-xs whitespace-nowrap pointer-events-none"
                      style={{
                        left: `${moonScreenX}px`,
                        top: '50%',
                        transform: `translate(-50%, calc(-50% - ${moonPixelRadius + 12}px))`,
                        zIndex: 25,
                        opacity: 0.9,
                        textShadow: '0 1px 2px rgba(0,0,0,1)'
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

      {/* Ruler Overlay */}
      <div className="absolute bottom-0 left-0 w-full h-16 pointer-events-none z-40 select-none overflow-hidden">
        <div className="absolute bottom-0 w-full border-b border-white/20"></div>

        {(() => {
          if (pixelsPerKm <= 0) return null;
          const visibleHalfWidthKm = (dimensions.width / 2) / pixelsPerKm;
          const startKm = Math.floor((cameraX - visibleHalfWidthKm) / RULER_TICK_KM) * RULER_TICK_KM;
          const endKm = Math.ceil((cameraX + visibleHalfWidthKm) / RULER_TICK_KM) * RULER_TICK_KM;

          const startIdx = Math.floor(startKm / RULER_TICK_KM);
          const endIdx = Math.ceil(endKm / RULER_TICK_KM);

          const ticks = [];
          if (endIdx - startIdx > 500) return null;

          for (let i = startIdx; i <= endIdx; i++) {
            const k = i * RULER_TICK_KM;
            const isMajor = Math.abs(k % RULER_MAJOR_TICK_KM) < (RULER_TICK_KM / 2);
            const screenX = getScreenX(k);

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
                  <div className="absolute bottom-7 left-1/2 -translate-x-1/2 text-xs font-bold text-white whitespace-nowrap drop-shadow-md">
                    {formatNumber(k / 1000000)}M
                  </div>
                )}
              </div>
            );
          }
          return ticks;
        })()}
      </div>

      {/* Debug Readout */}
      <div className="absolute bottom-4 left-4 font-mono text-green-400 bg-white/10 backdrop-blur-xl border border-white/20 p-4 rounded-xl pointer-events-none select-none z-50 shadow-[0_8px_32px_0_rgba(0,0,0,0.37)]">
        <div className="text-xs text-white/50 mb-1 uppercase tracking-wider">Distance from Sun</div>
        <div className="text-lg font-bold text-white">{formatNumber(Math.floor(cameraX))} km</div>
        <div className="text-sm text-green-400">{formatNumber(cameraX / AU_IN_KM, 6)} AU</div>
        <div className="text-sm text-blue-300">{formatNumber(cameraX / LIGHT_YEAR_IN_KM, 10)} ly</div>
        {autoTravelMode === 'light' && <div className="text-xs text-yellow-400 mt-2 animate-pulse font-bold">AUTO-PILOT ACTIVE (1c)</div>}
        {autoTravelMode === 'rocket' && <div className="text-xs text-orange-400 mt-2 animate-pulse font-bold">AUTO-PILOT ACTIVE (Starship)</div>}
      </div>

      {/* World-Space Info Text (Fixed at 1.2M km) */}
      {(() => {
        const infoKm = 1200000; // 1.2 Million km
        const infoScreenX = getScreenX(infoKm);

        // Only render if roughly on screen (optimization)
        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[500px] pointer-events-none select-none z-30 flex flex-col gap-4"
            style={{ left: `${infoScreenX}px` }}
          >
            {/* Block 1: Intro */}
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <h1 className="text-3xl font-bold mb-2 bg-gradient-to-r from-white to-white/60 bg-clip-text text-transparent">
                Real Scale Solar System Model
              </h1>
              <p className="text-lg font-light text-white/80 leading-relaxed">
                The distances and sizes of the planets are accurate. You might be surprised how empty space really is.
              </p>
              <p className="text-sm text-white/50 italic pt-1 border-t border-white/10 mt-2">
                It's largely nothing. Like a lot of nothing.
              </p>
            </div>

            {/* Block 2: Controls */}
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg space-y-3">
              <p className="text-base text-white/90">
                <span className="text-white font-medium">Ruler:</span> Shows your current distance to the Sun in Million Kilometers (located at the bottom of the screen).
              </p>
              <p className="text-base text-white/90">
                <span className="text-yellow-400 font-medium">Light Bulb:</span> Travel at speed of light (299.792 km/s).
              </p>
              <p className="text-base text-white/90">
                <span className="text-orange-400 font-medium">Rocket:</span> Travel at Starship speed (30 km/s ≈ 108.000 km/h).
              </p>
              <p className="text-base text-white/90">
                <span className="text-white/90 font-medium">Arrow Keys:</span> Use your arrow keys on your keyboard (left and right) to move at 5x speed of light.
              </p>
              <p className="text-sm text-white/50 italic pt-1 border-t border-white/10">
                (In reality, it is physically impossible to move beyond the speed of light. Ask your professor about it.)
              </p>
            </div>

            {/* Block 3: Units */}
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg space-y-2">
              <div className="text-base text-white/80">
                <strong className="text-green-400">1 Astronomical Unit (AU)</strong> = 149.597.870,700 km<br />
                <span className="text-sm text-white/50">(Distance Earth-Sun)</span>
              </div>
              <div className="text-base text-white/80">
                <strong className="text-blue-300">1 Light Year</strong> = ~9.460.000.000.000 km<br />
                <span className="text-sm text-white/50">(Distance light travels in 1 year)</span>
              </div>
            </div>
          </div>
        );
      })()}

      {/* Sun Size & Scale Info (Fixed at 2.5M km) */}
      {(() => {
        const infoKm = 2500000; // 2.5 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <h1 className="text-2xl font-bold mb-2 bg-gradient-to-r from-orange-300 to-red-500 bg-clip-text text-transparent">
                Solar Scale
              </h1>
              <p className="text-lg font-light text-white/80 leading-relaxed">
                The Sun has a radius of <span className="font-bold text-white">696.340 km</span>. In this model, all planets and distances are scaled precisely according to this massive size to show the true scale of our solar system.
              </p>
            </div>
          </div>
        );
      })()}

      {/* Speed of Light Info (Fixed at 4.5M km) */}
      {(() => {
        const infoKm = 4500000; // 4.5 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <h1 className="text-2xl font-bold mb-2 bg-gradient-to-r from-yellow-200 to-yellow-500 bg-clip-text text-transparent">
                Speed of Light
              </h1>
              <p className="text-lg font-light text-white/80 leading-relaxed">
                Light travels at about 300 thousand kilometers per second, or exactly <span className="font-bold text-white">299.792</span> kilometers per second.
              </p>
            </div>
          </div>
        );
      })()}

      {/* Distance Info Block 1 (Fixed at 7M km) */}
      {(() => {
        const infoKm = 7000000; // 7 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <p className="text-lg font-light text-white/80 leading-relaxed">
                Distances in space are measured differently then we are used to, our metrics break down in space. We change to Astronomical Units (AU) and Light Years. You can see the distance measure at the bottom left corner of your screen.
              </p>
            </div>
          </div>
        );
      })()}

      {/* Distance Info Block 2 (Fixed at 8M km) */}
      {(() => {
        const infoKm = 8000000; // 8 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <p className="text-lg font-light text-white/80 leading-relaxed">
                One AU is the distance from the sun to earth, or about 149 Million kilometers.
              </p>
            </div>
          </div>
        );
      })()}

      {/* New Info Block (Fixed at 8.5M km) */}
      {(() => {
        const infoKm = 8500000; // 8.5 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <p className="text-lg font-light text-white/80 leading-relaxed">
                We are now on our way to the first Planet of our Solar System.
              </p>
            </div>
          </div>
        );
      })()}

      {/* Mercury Info Block (Fixed at 12.5M km) */}
      {(() => {
        const infoKm = 12500000; // 12.5 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <h1 className="text-2xl font-bold mb-2 bg-gradient-to-r from-gray-300 to-gray-500 bg-clip-text text-transparent">
                Mercury
              </h1>
              <p className="text-lg font-light text-white/80 leading-relaxed">
                Mercury, the tiniest planet, races around the Sun in just 88 days, enduring days hotter than <span className="font-bold text-white">427°C (800°F)</span> and nights colder than <span className="font-bold text-white">-179°C (-290°F)</span> without an atmosphere to regulate heat.
              </p>
            </div>
          </div>
        );
      })()}

      {/* Info Block at 20M km */}
      {(() => {
        const infoKm = 20000000; // 20 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <p className="text-lg font-light text-white/80 leading-relaxed">
                We’ve now traveled more than 20 million kilometers—light from the Sun would take about one minute and seven seconds to reach this point. And we’re still not even halfway to Mercury…
              </p>
            </div>
          </div>
        );
      })()}

      {/* Info Block at 29M km */}
      {(() => {
        const infoKm = 29000000; // 29 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <p className="text-lg font-light text-white/80 leading-relaxed">
                OK, now we are half way to Mercury
              </p>
            </div>
          </div>
        );
      })()}

      {/* Info Block at 60M km */}
      {(() => {
        const infoKm = 60000000; // 60 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <p className="text-lg font-light text-white/80 leading-relaxed">
                The second planet is...
              </p>
            </div>
          </div>
        );
      })()}

      {/* Info Block at 65M km */}
      {(() => {
        const infoKm = 65000000; // 65 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <p className="text-lg font-light text-white/80 leading-relaxed">
                Venus, Earth's "twin" in size, boils at <span className="font-bold text-white">460°C (860°F)</span> thanks to a thick carbon dioxide atmosphere trapping heat.
              </p>
            </div>
          </div>
        );
      })()}

      {/* Info Block at 75M km */}
      {(() => {
        const infoKm = 75000000; // 75 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <p className="text-lg font-light text-white/80 leading-relaxed">
                Venus's pressure is 92 times Earth's, like being a kilometer underwater.
              </p>
            </div>
          </div>
        );
      })()}

      {/* Info Block at 90M km */}
      {(() => {
        const infoKm = 90000000; // 90 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <p className="text-lg font-light text-white/80 leading-relaxed">
                Mercury and Venus are the only Planets in the Solar System with no moons
              </p>
            </div>
          </div>
        );
      })()}

      {/* Info Block at 100M km */}
      {(() => {
        const infoKm = 100000000; // 100 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <p className="text-lg font-light text-white/80 leading-relaxed">
                We have now crossed 100 Million Kilometers. Light needs to travel 5 and a half minutes to reach this point.
              </p>
            </div>
          </div>
        );
      })()}

      {/* Info Block at 110M km */}
      {(() => {
        const infoKm = 110000000; // 110 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <p className="text-lg font-light text-white/80 leading-relaxed">
                We are now on our way to the planet that we call home
              </p>
            </div>
          </div>
        );
      })()}

      {/* Info Block at 112M km */}
      {(() => {
        const infoKm = 112000000; // 112 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <p className="text-lg font-light text-white/80 leading-relaxed">
                The Earth-Moon system stands out in the solar system due to the Moon's unusually large size relative to Earth, its unique formation from a massive ancient collision, and its profound stabilizing effects on Earth's climate and tides, which may have played a role in fostering life.
              </p>
            </div>
          </div>
        );
      })()}

      {/* Info Block at 155M km */}
      {(() => {
        const infoKm = 155000000; // 155 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <p className="text-lg font-light text-white/80 leading-relaxed">
                Now it is time to activate the rocket on the right side of your screen.
              </p>
            </div>
          </div>
        );
      })()}

      {/* Info Block at 158M km */}
      {(() => {
        const infoKm = 158000000; // 158 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <p className="text-lg font-light text-white/80 leading-relaxed">
                Elon Musk’s company SpaceX is planning the first human interplanetary mission to Mars using the largest rocket ever built
              </p>
            </div>
          </div>
        );
      })()}

      {/* Info Block at 162M km */}
      {(() => {
        const infoKm = 162000000; // 162 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <p className="text-lg font-light text-white/80 leading-relaxed">
                Starship could travel with speeds up to 36 thousand kilometers per hour, or 10 kilometers per second.
              </p>
            </div>
          </div>
        );
      })()}

      {/* Venus Info Block (Fixed at 61M km) */}
      {(() => {
        const infoKm = 61000000; // 61 Million km
        const infoScreenX = getScreenX(infoKm);

        if (infoScreenX < -500 || infoScreenX > dimensions.width + 500) return null;

        return (
          <div
            className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
            style={{ left: `${infoScreenX}px` }}
          >
            <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
              <h1 className="text-2xl font-bold mb-2 bg-gradient-to-r from-yellow-100 to-orange-200 bg-clip-text text-transparent">
                Venus
              </h1>
              <p className="text-lg font-light text-white/80 leading-relaxed">
                Venus spins backward compared to most planets, with a day lasting longer than its year—243 Earth days to rotate once. Thick clouds of sulfuric acid trap heat, making it the hottest world at over <span className="font-bold text-white">460°C (860°F)</span>, hotter even than Mercury despite being farther from the Sun.
              </p>
            </div>
          </div>
        );
      })()}


      {/* Starship Mission Info Blocks (Between Earth and Mars) */}
      {
        (() => {
          const blocks = [
            {
              km: 160000000, // 160M km
              text: "Traditional chemical-propulsion missions to Mars (using low-energy trajectories) typically take about 6 to 9 months."
            },
            {
              km: 170000000, // 170M km
              text: "Recent studies suggest that using Starship (with high-energy trajectories and refuelling) could reduce that transit time to about 90 to 104 days (≈ 3 months) depending on alignment of Earth and Mars."
            },
            {
              km: 180000000, // 180M km
              text: "It is propably better to switch to speed of light again, otherwise it will take a very long time to reach Mars."
            },
            {
              km: 190000000, // 190M km
              text: "The actual “in-space speed” varies greatly depending on the transfer trajectory (how much delta-v is used, when the burn occurs, how much deceleration/arrival burn). The 90-day mission profile assumes significantly higher energy than a standard Hohmann transfer."
            },
            {
              km: 200000000, // 200M km
              text: "We are now 200 Million kilometers away from the sun. Light from the sun needs to travel about 11 Minutes to this point. Remember, light travels at about 300 thousand kilometers per second."
            },
            {
              km: 203000000, // 203M km
              text: "We are slowly approaching..."
            },
            {
              km: 205000000, // 205M km
              text: "Mars, the Red Planet, boasts the solar system's tallest mountain, Olympus Mons, which stands 22 km (13.6 miles) high, about 2.5 times the height of Mount Everest at 8.8 km."
            },
            {
              km: 210000000, // 210M km
              text: "Ancient riverbeds and valley networks on Mars suggest that liquid water once flowed across its surface billions of years ago, carving channels that hint at a warmer, wetter past before the planet's atmosphere thinned."
            },
            {
              km: 215000000, // 215M km
              text: "Mars has two moons. Phobos, Mars's larger moon, orbits just 6,000 km above the surface, completing a circuit about every 8 hours, causing it to rise and set twice daily from the Martian equator. Due to the proximity, Phobos is spiraling inward at a rate of about 2 cm per year—or 2 meters per century. in 30-50 Million years it will probably be torn apart and partly crash into Mars and partly forn a ring around the planet."
            },
            {
              km: 218000000, // 218M km
              text: "Deimos, the smaller moon of Mars, is likely a captured asteroid from the outer solar system, orbiting at a more distant 23,460 km and taking about 30 hours to complete one revolution."
            },
            {
              km: 221000000, // 221M km
              text: "Mars' moons are named after the sons of the Greek god of war: Phobos for fear and Deimos for panic, reflecting their ominous, irregular shapes and cratered surfaces."
            },
            {
              km: 224000000, // 224M km
              text: "Here it is, Mars and the two Moons. And no, the moons are actually that close to the Planet. Compare that to our Moon."
            },
            {
              km: 230000000, // 230M km
              text: "Now get ready for a long wait. Because the next planet, Jupiter is very, very far away. From now on, the distances are going to escalate a bit..."
            },
            {
              km: 231000000, // 231M km
              text: "But.."
            },
            {
              km: 234000000, // 234M km
              text: "Between Mars and Jupiter"
            },
            {
              km: 236000000, // 236M km
              text: "There is the Asteroid Belt"
            },
            {
              km: 238000000, // 238M km
              text: "The Asteroid Belt, located between the orbits of Mars and Jupiter, contains millions or even billions of rocky fragments that are leftovers from the solar system's early formation, ranging from dust-sized particles to larger bodies."
            },
            {
              km: 240000000, // 240M km
              text: "Jupiter's immense gravity disrupted the accretion process in this region, preventing these fragments from coalescing into a full-fledged planet billions of years ago."
            },
            {
              km: 244000000, // 244M km
              text: "Ceres, the largest object in the Asteroid Belt, is classified as a dwarf planet with a diameter of about 940 km; it features cryovolcanoes that erupt icy material and may harbor a subsurface ocean of briny water."
            },
            {
              km: 248000000, // 248M km
              text: "Most asteroids are irregularly shaped due to collisions and low gravity, with sizes varying from tiny pebbles to substantial bodies like Vesta, which measures up to 525 km across and has a massive impact crater."
            },
            {
              km: 252000000, // 252M km
              text: "The Asteroid Belt spans from approximately 2.2 to 3.2 astronomical units (AU) from the Sun, forming a vast, doughnut-shaped ring of stone, metal, and ice that encircles the inner planets."
            },
            {
              km: 256000000, // 256M km
              text: "Contrary to depictions in movies, asteroids in the belt are widely spaced, with average distances of millions of kilometers between them, making close encounters during traversal highly unlikely."
            },
            {
              km: 406000000, // 406M km
              text: "We are now leaving the inner Solar System and we are now on our way to the gas giants."
            },
            {
              km: 410000000, // 410M km
              text: "Jupiter, the colossal king of the planets, boasts a diameter of 142,984 kilometers at its equator—over 11 times that of Earth—and a mass 318 times greater, allowing it to contain about 1,321 Earth volumes within its gaseous layers."
            },
            {
              km: 415000000, // 415M km
              text: "Jupiter's atmosphere harbors the Great Red Spot, a massive anticyclonic storm that has raged for at least 190 years, with current dimensions measuring approximately 14,000 kilometers along its long axis as of 2024—still wider than Earth's 12,742-kilometer diameter but shrinking at a rate of about 930 kilometers per decade."
            },
            {
              km: 420000000, // 420M km
              text: "Jupiter's magnetic field, the strongest among planets, is 16 to 54 times more powerful than Earth's, generated by electrical currents in its layer of liquid metallic hydrogen under immense pressure."
            },
            {
              km: 425000000, // 425M km
              text: "This dynamo creates a vast magnetosphere that shields the planet and its moons from solar wind while trapping charged particles in intense radiation belts thousands of times stronger than Earth's Van Allen belts."
            },
            {
              km: 430000000, // 430M km
              text: "Jupiter's immense magnetosphere extends 1 to 3 million kilometers (600,000 to 2 million miles) toward the Sun and trails over 965 million kilometers (600 million miles) behind, interacting with its moons like Io to create a plasma torus and fueling spectacular auroras at the poles visible in ultraviolet, infrared, and X-ray wavelengths."
            },
            {
              km: 435000000, // 435M km
              text: "Jupiter has 97 confirmed moons, making it the planet with the second-most satellites in the solar system after Saturn, though the exact count can fluctuate with new discoveries from ongoing astronomical surveys."
            },
            {
              km: 440000000, // 440M km
              text: "The four largest moons—Io, Europa, Ganymede, and Callisto—were discovered by Galileo Galilei in 1610 and account for nearly all the mass orbiting Jupiter."
            },
            {
              km: 445000000, // 445M km
              text: "Io: The Volcanic Powerhouse. Io is renowned for its extreme volcanism, with over 400 active volcanoes spewing lava plumes up to 500 km high, driven by tidal forces from Jupiter. Slightly larger than Earth's Moon at 3,643 km in diameter, its surface is a colorful mosaic of sulfur compounds, constantly resurfaced to erase craters."
            },
            {
              km: 450000000, // 450M km
              text: "Europa, at 3,122 km across, features a smooth icy crust cracked by reddish lines, beneath which lies a global saltwater ocean possibly twice Earth's volume. This makes it a top candidate for extraterrestrial life, with chemical ingredients and energy sources like hydrothermal vents potentially present. NASA's Europa Clipper, launched in October 2024, aims to assess its habitability."
            },
            {
              km: 455000000, // 455M km
              text: "Ganymede: The Giant with a Field. As the solar system's largest moon at 5,268 km—bigger than Mercury—Ganymede uniquely generates its own magnetic field from an iron core, creating auroras. Its surface mixes ancient craters with younger grooves, and evidence suggests a layered subsurface ocean with more water than Earth's surface."
            },
            {
              km: 460000000, // 460M km
              text: "Callisto, Jupiter's second-largest moon at 4,821 km, is the most heavily cratered body known, preserving a 4-billion-year record of impacts. It may harbor a salty subsurface ocean, and its location outside intense radiation belts makes it a candidate for future exploration bases."
            },
            {
              km: 790000000, // 790M km
              text: "Saturn's rings, spanning up to 282,000 kilometers but often as thin as 10 meters, are made of ice, rock, and dust chunks ranging from tiny grains to mountain-sized boulders, likely remnants of shattered moons or comets."
            },
            {
              km: 795000000, // 795M km
              text: "Titan, Saturn's largest moon at 5,150 kilometers in diameter, is the only moon with a substantial atmosphere and the only known world besides Earth with stable liquid bodies on its surface—rivers, lakes, and seas of methane and ethane."
            },
            {
              km: 800000000, // 800M km
              text: "We have now crossed 800 Million Kilometers. Saturn is almost double the distance from here."
            },
            {
              km: 805000000, // 805M km
              text: "Titan, Saturn's largest moon at 5,150 kilometers in diameter, is the only moon with a substantial atmosphere and the only known world besides Earth with stable liquid bodies on its surface—rivers, lakes, and seas of methane and ethane."
            },
            {
              km: 465000000, // 465M km
              text: "I recommend to press the button on the top bar, which will teleport you to Jupiter."
            },
            {
              km: 782000000, // 782M km
              text: "We are now heading to Saturn."
            },
            {
              km: 788000000, // 788M km
              text: "Saturn stands out as the solar system's ringed jewel, with its vast, intricate ring system composed of billions of icy particles that create a stunning visual spectacle visible even from Earth."
            },
            {
              km: 810000000, // 810M km
              text: "Light takes about 80 Minutes from the Sun to reach Saturn."
            },
            {
              km: 1429000000, // 1429M km
              text: "The scale of the Solar System increases dramatically beyond Mars."
            },
            {
              km: 1431000000, // 1431M km
              text: "The first four planets—Mercury through Mars—are each spaced roughly 50 million kilometers apart."
            },
            {
              km: 1434000000, // 1434M km
              text: "The spacing of the gas giants increases exponentially."
            },
            {
              km: 1437000000, // 1437M km
              text: "Jupiter orbits about five times farther from the Sun than Earth."
            },
            {
              km: 1440000000, // 1440M km
              text: "Saturn is roughly twice as far from the Sun as Jupiter."
            },
            {
              km: 1444000000, // 1444M km
              text: "Uranus, in turn, is about twice as far from the Sun as Saturn."
            },
            {
              km: 1450000000, // 1450M km
              text: "And then we have the last Planet of our Solar System, Neptun..."
            },
            {
              km: 1453000000, // 1453M km
              text: "Uranus, the seventh planet from the Sun, orbits at an average distance of about 19.2 AU (approximately 2.87 billion kilometers), making the journey from Saturn (at 9.5 AU) span roughly 9.7 AU of vast, empty space."
            },
            {
              km: 1456000000, // 1456M km
              text: "It is an ice giant with a composition dominated by hydrogen, helium, and methane, giving its atmosphere a distinctive blue-green hue."
            },
            {
              km: 1460000000, // 1460M km
              text: "Uranus features a unique axial tilt of nearly 98 degrees, causing it to essentially roll on its side as it orbits, leading to extreme seasonal variations where each pole experiences 42 years of continuous sunlight or darkness."
            },
            {
              km: 1465000000, // 1465M km
              text: "The planet is encircled by 13 faint, dark rings composed mainly of dust and rock particles, discovered in 1977, which are much narrower and less prominent than Saturn's."
            },
            {
              km: 1470000000, // 1470M km
              text: "Uranus has 28 known moons, with the five largest—Miranda, Ariel, Umbriel, Titania, and Oberon—being icy worlds named after Shakespearean characters, and Titania as the biggest at about 1,578 km in diameter."
            },
            {
              km: 1475000000, // 1475M km
              text: "Uranus stands out as one of the solar system's ice giants, with a chilly average temperature of -224°C (-371°F) and a diameter of about 51,118 km, making it the third-largest planet. Its atmosphere absorbs red light due to methane, resulting in the cyan appearance, and it completes an orbit around the Sun every 84 Earth years."
            },
            {
              km: 1480000000, // 1480M km
              text: "Among the moons, Miranda boasts dramatic cliffs up to 20 km high, while Oberon and Titania show ancient craters and possible subsurface oceans, adding intrigue to Uranus's family of satellites."
            },
            {
              km: 2873000000, // 2873M km
              text: "The Uranus-Neptune gap, averaging 10.8 AU, highlights increasing isolation in the outer reaches, with travel time for light about 1.5 hours between them."
            },
            {
              km: 2876000000, // 2876M km
              text: "Neptune's orbit takes 165 Earth years, crossing Pluto's path but avoiding collisions due to resonances."
            },
            {
              km: 2880000000, // 2880M km
              text: "Neptune hosts 16 known moons, dominated by Triton—the seventh-largest in the solar system at 2,707 kilometers in diameter—which orbits retrograde at 354,800 kilometers, suggesting Kuiper Belt capture about 3.6 billion years ago"
            },
            {
              km: 2885000000, // 2885M km
              text: "Unique to Neptune are its supersonic winds, which reach speeds up to 2000 km/h, twice the supersonic speed."
            },
            {
              km: 2900000000, // 2900M km
              text: "Triton is unusual because it is the only large moon in our solar system that orbits in the opposite direction of its planet's rotation―a retrograde orbit."
            },
            {
              km: 4500000000, // 4500M km
              text: "Beyond Neptune lies the Kuiper Belt, a vast ring of icy bodies starting around 30 AU from the Sun, home to dwarf planets like Pluto and Eris, and serving as a reservoir for short-period comets."
            },
            {
              km: 4505000000, // 4505M km
              text: "Pluto, reclassified as a dwarf planet in 2006, orbits in the Kuiper Belt with five moons, including Charon, and features a thin nitrogen atmosphere that forms seasonal frost caps."
            },
            {
              km: 4510000000, // 4510M km
              text: "Pluto's surface displays diverse geology, from nitrogen ice plains to water ice mountains up to 3 km high, as revealed by NASA's New Horizons in 2015. Its heart-shaped Tombaugh Regio is a vast glacier of frozen nitrogen, methane, and carbon monoxide. Pluto orbits eccentrically from 30 to 50 AU over 248 years."
            },
            {
              km: 4515000000, // 4515M km
              text: "The Kuiper Belt extends from 30 to 50 AU, containing over 100,000 objects larger than 100 km, including dwarf planets like Haumea and Makemake. It formed from remnants of the solar system's early disc, perturbed by Neptune's gravity."
            },
            {
              km: 4520000000, // 4520M km
              text: "Beyond the Kuiper Belt lies the Oort Cloud. The Oort Cloud forms a spherical shell from 2,000 to 100,000 AU, holding trillions of icy bodies that become long-period comets when disturbed by passing stars. It's theorized but not directly observed, marking the solar system's true edge."
            },
            {
              km: 4524000000, // 4524M km
              text: "Expanding outward, the scattered disc blends into the distant fringes, hosting objects like Sedna—a 995 km reddish body with a perihelion of 76 AU and an aphelion of 937 AU, completing an orbit in 11,400 years, potentially perturbed by an undiscovered Planet Nine hypothesized to be 5-10 Earth masses at 400-800 AU."
            }

          ];

          return blocks.map((block, index) => {
            const screenX = getScreenX(block.km);
            if (screenX < -500 || screenX > dimensions.width + 500) return null;

            return (
              <div
                key={index}
                className="absolute top-[55%] -translate-y-1/2 text-white w-[400px] pointer-events-none select-none z-30"
                style={{ left: `${screenX}px` }}
              >
                <div className="bg-white/10 backdrop-blur-md border border-white/20 rounded-xl p-6 shadow-lg">
                  <p className="text-lg font-light text-white/80 leading-relaxed">
                    {block.text}
                  </p>
                </div>
              </div>
            );
          });
        })()
      }

      {/* Rocket Button */}
      <button
        onClick={toggleRocketTravel}
        className={`absolute bottom-28 right-8 p-4 rounded-full transition-all duration-500 border z-50 outline-none focus:outline-none backdrop-blur-xl shadow-[0_8px_32px_0_rgba(0,0,0,0.37)] ${autoTravelMode === 'rocket'
          ? 'bg-orange-500/20 border-orange-400/50 text-orange-400 shadow-[0_0_30px_rgba(249,115,22,0.3)] scale-110'
          : 'bg-white/10 border-white/20 text-white/80 hover:bg-white/20 hover:text-white hover:scale-105 hover:border-white/40'
          }`}
        title={autoTravelMode === 'rocket' ? "Stop Starship Travel" : "Engage Auto-Pilot (Starship Speed)"}
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z" />
          <path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z" />
          <path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0" />
          <path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5" />
        </svg>
      </button>

      {/* Light Bulb Button */}
      <button
        onClick={toggleLightTravel}
        className={`absolute bottom-8 right-8 p-4 rounded-full transition-all duration-500 border z-50 outline-none focus:outline-none backdrop-blur-xl shadow-[0_8px_32px_0_rgba(0,0,0,0.37)] ${autoTravelMode === 'light'
          ? 'bg-yellow-500/20 border-yellow-400/50 text-yellow-400 shadow-[0_0_30px_rgba(250,204,21,0.3)] scale-110'
          : 'bg-white/10 border-white/20 text-white/80 hover:bg-white/20 hover:text-white hover:scale-105 hover:border-white/40'
          }`}
        title={autoTravelMode === 'light' ? "Stop Light-Speed Travel" : "Engage Auto-Pilot (1x Speed of Light)"}
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-1 1.5-2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5" />
          <path d="M9 18h6" />
          <path d="M10 22h4" />
        </svg>
      </button>

    </div>
  );
};
