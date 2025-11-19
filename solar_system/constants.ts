
/**
 * Real physical constants (in Kilometers)
 */

// Speed of light in vacuum (km/s)
export const SPEED_OF_LIGHT_KM_S = 299792;

// The multiplier requested (5x speed of light)
export const MOVEMENT_SPEED_MULTIPLIER = 5;

// Effective movement speed (km/s)
export const TRAVEL_SPEED_KM_S = SPEED_OF_LIGHT_KM_S * MOVEMENT_SPEED_MULTIPLIER;

// Astronomical Unit in km
export const AU_IN_KM = 149597870;

// Radius of the Sun (km)
// Source: NASA/Standard solar models (approximate)
export const SUN_RADIUS_KM = 696340;

// Initial Camera Position
// We start at the surface of the Sun so the edge is in the center of the screen
export const INITIAL_CAMERA_X_KM = SUN_RADIUS_KM;

/**
 * Planet & Moon Data
 * Distances are average distance from Sun (Semi-major axis) for planets
 * Distances are average distance from Planet center for moons
 */
export interface MoonData {
  name: string;
  radiusKm: number;
  distanceFromPlanetKm: number;
  color: string;
}

export interface RingData {
  innerRadiusKm: number;
  outerRadiusKm: number;
  color: string;
  opacity: number;
  rotationDeg?: number;
}

export interface PlanetData {
  name: string;
  radiusKm: number;
  distanceFromSunKm: number;
  color: string;
  moons?: MoonData[];
  rings?: RingData;
}

export const PLANETS: PlanetData[] = [
  { 
    name: 'Mercury', 
    radiusKm: 2440, 
    distanceFromSunKm: 0.387 * AU_IN_KM, 
    // Updated color to a darker grey with subtle variation to hint at Mercury's cratered surface
    color: '#5C5C5C' 
  },
  { 
    name: 'Venus', 
    radiusKm: 6052, 
    distanceFromSunKm: 0.723 * AU_IN_KM, 
    // Updated color to a softer yellow/cream tone to evoke Venus's cloudy atmosphere
    color: '#EEDBAD' 
  },
  { 
    name: 'Earth', 
    radiusKm: 6371, 
    distanceFromSunKm: 1.000 * AU_IN_KM, 
    // Updated color to a deeper blue with subtle hints of green and white will be applied via CSS
    color: '#2A8AC7',
    moons: [
      {
        name: 'Moon',
        radiusKm: 1737,
        distanceFromPlanetKm: 384400,
        color: '#D1D1D1'
      }
    ]
  },
  { 
    name: 'Mars', 
    radiusKm: 3390, 
    distanceFromSunKm: 1.524 * AU_IN_KM, 
    // Updated color to a richer rusty red to better reflect Mars's surface
    color: '#B4512A',
    moons: [
      {
        name: 'Phobos',
        radiusKm: 11,
        distanceFromPlanetKm: 9376,
        color: '#B08A75'
      },
      {
        name: 'Deimos',
        radiusKm: 6,
        distanceFromPlanetKm: 23463,
        color: '#C8B099'
      }
    ]
  },
  { 
    name: 'Jupiter', 
    radiusKm: 69911, 
    distanceFromSunKm: 5.203 * AU_IN_KM, 
    color: '#D6A566',
    moons: [
      {
        name: 'Io',
        radiusKm: 1821.6,
        distanceFromPlanetKm: 421700,
        color: '#F8F4A6'
      },
      {
        name: 'Europa',
        radiusKm: 1560.8,
        distanceFromPlanetKm: 671100,
        color: '#C0C7CB'
      },
      {
        name: 'Ganymede',
        radiusKm: 2634.1,
        distanceFromPlanetKm: 1070400,
        color: '#9C9185'
      },
      {
        name: 'Callisto',
        radiusKm: 2410.3,
        distanceFromPlanetKm: 1882700,
        color: '#756F63'
      }
    ]
  },
  { 
    name: 'Saturn', 
    radiusKm: 58232, 
    distanceFromSunKm: 9.537 * AU_IN_KM, 
    color: '#EAD6B8',
    rings: {
      innerRadiusKm: 74500, // Start of C Ring
      outerRadiusKm: 140220, // End of A Ring
      color: '#DAC4A2', // Beige-gold
      opacity: 0.6,
      rotationDeg: 27
    },
    moons: [
      {
        name: 'Rhea',
        radiusKm: 763.8,
        distanceFromPlanetKm: 527108,
        color: '#C0C0C0'
      },
      {
        name: 'Titan',
        radiusKm: 2574.7,
        distanceFromPlanetKm: 1221870,
        color: '#E3C868'
      }
    ]
  },
  { 
    name: 'Uranus', 
    radiusKm: 25362, 
    distanceFromSunKm: 19.191 * AU_IN_KM, 
    color: '#D1E7E7',
    rings: {
      innerRadiusKm: 38000,
      outerRadiusKm: 51149, // Epsilon ring
      color: '#8A939C', // Dark faint grey
      opacity: 0.4,
      rotationDeg: 90
    }
  },
  { 
    name: 'Neptune', 
    radiusKm: 24622, 
    distanceFromSunKm: 30.069 * AU_IN_KM, 
    color: '#5B5DDF' 
  },
];

// Asteroid Belt (approximate main belt zone)
export const ASTEROID_BELT_START_KM = 2.2 * AU_IN_KM;
export const ASTEROID_BELT_END_KM = 3.2 * AU_IN_KM;

/**
 * Rendering Constants
 */

// How much larger than the viewport height should the Sun be?
// 1.5 means the Sun's diameter will be 150% of the screen height.
export const SUN_VIEWPORT_RATIO = 1.5;
