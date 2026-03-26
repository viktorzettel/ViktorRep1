import * as THREE from 'three';

// --- Constants (1 unit = 1 km) ---
const JUPITER_RADIUS = 69911;
const EUROPA_RADIUS = 1560;
const IO_RADIUS = 1822;
const GANYMEDE_RADIUS = 2634;
const CALLISTO_RADIUS = 2410;
const SUN_RADIUS = 696340; // True Radius
const SUN_DISTANCE = 778500000; // True Distance (5.2 AU)
const DESCENT_SPEED = 25; // km/s
const WARP_SPEED = 3000; // 1% Speed of Light
const START_DISTANCE = JUPITER_RADIUS + 1000000; // Start at 150k km altitude
const STOP_DISTANCE = JUPITER_RADIUS; // Stop exactly at surface (0km)
const SPEED_OF_LIGHT = 299792; // Real Speed of Light (km/s)
const EARTH_RADIUS = 6371;
const EARTH_DISTANCE = 149600000; // 1 AU
const MOON_RADIUS = 1737;
const MOON_DISTANCE = 384400;
const CAMERA_HEIGHT = 0.5;
const MERCURY_RADIUS = 2440;
const MERCURY_DISTANCE = 57900000; // 57.9 M km
const URANUS_RADIUS = 25362;
const URANUS_DISTANCE = 2870000000; // 2.87 B km
const SATURN_RADIUS = 58232;
const SATURN_DISTANCE = 1434000000; // 1.43 B km
const SATURN_TILT = 26.73 * Math.PI / 180;
const VENUS_RADIUS = 6051;
const VENUS_DISTANCE = 108200000; // 108.2 M km
const MARS_RADIUS = 3389;
const MARS_DISTANCE = 227900000; // 227.9 M km
const NEPTUNE_RADIUS = 24622;
const NEPTUNE_DISTANCE = 4495000000; // 4.495 B km
// --- Scene Setup ---
const scene = new THREE.Scene();

// 1. RENDERER IMPROVEMENTS
// We use a logarithmic depth buffer to handle the massive scale differences (1km vs 700,000km)
const renderer = new THREE.WebGLRenderer({
    antialias: true,
    logarithmicDepthBuffer: true
});
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(window.devicePixelRatio);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

// REALISM UPGRADE: Tone Mapping
// This allows us to use very bright lights (Sun) without them looking flat white.
// It mimics how a human eye or camera adjusts to bright light.
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.3;

document.body.appendChild(renderer.domElement);

const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 1000000000000); // Far plane 1 Trillion

// --- Lighting & Sun ---

// Ambient Light: In space, shadows are almost pitch black. 
// We keep a tiny amount so the dark side isn't invisible, but very faint.
const ambientLight = new THREE.AmbientLight(0xffffff, 0.02);
scene.add(ambientLight);

// Fog for Atmospheric Entry
const fogColor = new THREE.Color(0xcc8855); // Jupiter Orange/Brown
scene.fog = new THREE.FogExp2(fogColor, 0.0); // Start with no fog

// Sun Position: 
// True Distance: 778.5 Million km
// Direction: Normalized vector from previous (5, 0.32, 3) * SUN_DISTANCE
// Y adjusted for 3.13 degree tilt: tan(3.13) * DistanceXZ
const sunPosition = new THREE.Vector3(0.85 * SUN_DISTANCE, 0.054 * SUN_DISTANCE, 0.51 * SUN_DISTANCE);

// REALISM UPGRADE: High Intensity Light
const sunLight = new THREE.DirectionalLight(0xffffff, 2.0); // Increased intensity by 20%
sunLight.position.copy(sunPosition);
sunLight.castShadow = true;

// Shadow Configuration
const d = 800000; // Shadow box size
sunLight.shadow.camera.left = -d;
sunLight.shadow.camera.right = d;
sunLight.shadow.camera.top = d;
sunLight.shadow.camera.bottom = -d;
sunLight.shadow.camera.near = SUN_DISTANCE - 1000000; // Start shadow cam near Jupiter
sunLight.shadow.camera.far = SUN_DISTANCE + 1000000; // End it just past Jupiter
sunLight.shadow.mapSize.width = 4096;
sunLight.shadow.mapSize.height = 4096;
sunLight.shadow.bias = -0.00005;
scene.add(sunLight);

// --- Visual Sun (The Glare) ---

// 1. The Core (The actual star disk)
// 1. The Core (The actual star disk)
const sunGeometry = new THREE.SphereGeometry(SUN_RADIUS, 256, 256);
const sunMeshMaterial = new THREE.MeshBasicMaterial({ color: 0xffffff });
sunMeshMaterial.color.setScalar(1000); // Increased brightness for true scale
const sunMesh = new THREE.Mesh(sunGeometry, sunMeshMaterial);
sunMesh.position.copy(sunPosition);
scene.add(sunMesh);

// 2. The Glare (Lens Diffraction)
// This simulates the light hitting the camera lens. 
// In vacuum, this is sharp, not cloudy.
function createGlowTexture() {
    const canvas = document.createElement('canvas');
    canvas.width = 128;
    canvas.height = 128;
    const ctx = canvas.getContext('2d');
    const gradient = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);

    // Core is blinding white
    gradient.addColorStop(0, 'rgba(255, 255, 255, 1)');
    // Sharp falloff (Vacuum look)
    gradient.addColorStop(0.2, 'rgba(255, 255, 255, 0.8)');
    gradient.addColorStop(0.4, 'rgba(255, 255, 240, 0.2)');
    gradient.addColorStop(1, 'rgba(0, 0, 0, 0)');

    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, 128, 128);
    return new THREE.CanvasTexture(canvas);
}

const glowMaterial = new THREE.SpriteMaterial({
    map: createGlowTexture(),
    color: 0xffffff, // Pure white glare
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false // Don't block stars behind it
});
const sunGlow = new THREE.Sprite(glowMaterial);
// Scale: roughly 4x the sun radius
sunGlow.scale.set(SUN_RADIUS * 6, SUN_RADIUS * 6, 1);
sunGlow.position.copy(sunPosition);
scene.add(sunGlow);

const sunLabel = createLabel('Sun');
sunLabel.scale.set(400000000, 100000000, 1); // 4:1 Ratio (Massive)
sunLabel.position.copy(sunPosition);
sunLabel.position.y += SUN_RADIUS + 25000000; // Offset above sun (50M km)
scene.add(sunLabel);

// Helper for round points
function createPointTexture() {
    const canvas = document.createElement('canvas');
    canvas.width = 32;
    canvas.height = 32;
    const ctx = canvas.getContext('2d');

    // Draw solid white circle
    ctx.beginPath();
    ctx.arc(16, 16, 14, 0, Math.PI * 2);
    ctx.fillStyle = '#ffffff';
    ctx.fill();

    return new THREE.CanvasTexture(canvas);
}

// --- Distant Sun Point (Sharp, fixed-size star) ---
const sunPointGeometry = new THREE.BufferGeometry();
sunPointGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array([sunPosition.x, sunPosition.y, sunPosition.z]), 3));
const sunPointMaterial = new THREE.PointsMaterial({
    color: 0xffffff,
    map: createPointTexture(), // Use circle texture
    size: 8, // Increased for visibility
    sizeAttenuation: false,
    transparent: true,
    alphaTest: 0.5, // Clip usage
    depthWrite: false
});
sunPointMaterial.color.setScalar(1000); // Force max brightness
const sunPoint = new THREE.Points(sunPointGeometry, sunPointMaterial);
scene.add(sunPoint);
sunPoint.visible = true; // Default for Jupiter start

// --- Starfield (Realistic Exposure) ---

let starField, smallStars;

function createStarfield() {
    // 1. Bright, Crisp Stars (Main visible stars)
    const starsGeometry = new THREE.BufferGeometry();
    const count = 6000; // Increased density for space realism
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);

    const color = new THREE.Color();

    for (let i = 0; i < count; i++) {
        // Position on a very distant sphere
        const r = 100000000000; // 100 Billion km
        const theta = 2 * Math.PI * Math.random();
        const phi = Math.acos(2 * Math.random() - 1);

        positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
        positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
        positions[i * 3 + 2] = r * Math.cos(phi);

        // Color: Mostly white, but with subtle realistic tints (blue/yellow)
        // Star types: O/B (Blue-white), G (Yellow-white), M (Red-orange)
        const starType = Math.random();
        if (starType > 0.9) color.setHex(0xaaaaff); // Blueish
        else if (starType > 0.7) color.setHex(0xffddaa); // Yellowish
        else color.setHex(0xffffff); // White

        // Random brightness variation
        const intensity = 0.5 + Math.random() * 0.5;
        colors[i * 3] = color.r * intensity;
        colors[i * 3 + 1] = color.g * intensity;
        colors[i * 3 + 2] = color.b * intensity;
    }

    starsGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    starsGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    const starsMaterial = new THREE.PointsMaterial({
        size: 1.2, // Smaller size for "crisp" look (was 1.5)
        sizeAttenuation: false,
        vertexColors: true,
        transparent: false, // Opaque for sharpness
        opacity: 1.0,
        depthWrite: false
    });

    starField = new THREE.Points(starsGeometry, starsMaterial);
    scene.add(starField);

    // 2. Background "Dust" Stars (Faint background stars)
    const smallStarsGeo = new THREE.BufferGeometry();
    const smallCount = 5000;
    const smallPos = new Float32Array(smallCount * 3);
    for (let i = 0; i < smallCount; i++) {
        const r = 100000000000; // 100 Billion km
        const theta = 2 * Math.PI * Math.random();
        const phi = Math.acos(2 * Math.random() - 1);
        smallPos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
        smallPos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
        smallPos[i * 3 + 2] = r * Math.cos(phi);
    }
    smallStarsGeo.setAttribute('position', new THREE.BufferAttribute(smallPos, 3));
    const smallStarsMat = new THREE.PointsMaterial({
        size: 0.8, // Tiny sharp dots (was 1.0)
        sizeAttenuation: false,
        color: 0x888888, // Dimmer gray
        transparent: true,
        opacity: 0.8,
        depthWrite: false
    });
    smallStars = new THREE.Points(smallStarsGeo, smallStarsMat);
    scene.add(smallStars);
}

createStarfield();

// --- Planetary Bodies ---

const textureLoader = new THREE.TextureLoader();

// JUPITER
const jupiterTexture = textureLoader.load('/textures/jupiter.jpg');
jupiterTexture.anisotropy = renderer.capabilities.getMaxAnisotropy(); // Max Sharpness
const jupiterGeometry = new THREE.SphereGeometry(JUPITER_RADIUS, 256, 256); // Smoother edge
const jupiterMaterial = new THREE.MeshStandardMaterial({
    map: jupiterTexture,
    // REALISM UPGRADE: Material Physics
    roughness: 1.0, // Gas clouds are matte, not shiny
    metalness: 0.0,
});
const jupiter = new THREE.Mesh(jupiterGeometry, jupiterMaterial);
jupiter.castShadow = true;
jupiter.receiveShadow = true;
scene.add(jupiter);
sunLight.target = jupiter;

// EUROPA
const europaTexture = textureLoader.load('/textures/europa2.jpg');
europaTexture.anisotropy = renderer.capabilities.getMaxAnisotropy();
const europaGeometry = new THREE.SphereGeometry(EUROPA_RADIUS, 128, 128);
const europaMaterial = new THREE.MeshStandardMaterial({
    map: europaTexture,
    roughness: 0.9, // Icy surface, slightly less rough than gas
    metalness: 0.1
});
const europa = new THREE.Mesh(europaGeometry, europaMaterial);
// Positioned to the left and slightly forward (towards camera) ~30 deg rotation
europa.position.set(-581000, 0, 335000);
europa.castShadow = true;
europa.receiveShadow = true;
scene.add(europa);

// LABEL
// LABEL
function createLabel(text) {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    canvas.width = 1024; // 4:1 Aspect Ratio
    canvas.height = 256;

    // Sophisticated Font
    context.font = '600 80px "Rajdhani", sans-serif'; // Thicker weight for visibility
    context.fillStyle = 'rgba(0, 255, 255, 1)'; // Cyan Text
    context.textAlign = 'center';
    context.textBaseline = 'middle';

    // Cyan Glow
    context.shadowColor = "rgba(0, 255, 255, 0.6)";
    context.shadowBlur = 15;

    // Clean Text (Normal spacing to fix "elongation")
    const labelText = text.toUpperCase();
    context.fillText(labelText, 512, 110);

    // Elegant Underline
    context.beginPath();
    context.moveTo(312, 160);
    context.lineTo(712, 160);
    context.lineWidth = 4;
    context.strokeStyle = 'rgba(0, 255, 255, 0.8)';
    context.stroke();

    const texture = new THREE.CanvasTexture(canvas);
    const material = new THREE.SpriteMaterial({
        map: texture,
        transparent: true,
        depthWrite: false,
        color: 0xffffff // Use texture colors directly
    });
    return new THREE.Sprite(material);
}

const europaLabel = createLabel('Europa');
// Correct aspect ratio (4:1) to prevent squashing/stretching
europaLabel.scale.set(200000, 50000, 1);
europaLabel.position.copy(europa.position);
europaLabel.position.y += EUROPA_RADIUS + 10000; // Standardized to 10k km
scene.add(europaLabel);

// (Removed Jupiter Return Marker)

// IO
// IO
const ioTexture = textureLoader.load('/textures/io.png');
ioTexture.anisotropy = renderer.capabilities.getMaxAnisotropy();
const ioGeometry = new THREE.SphereGeometry(IO_RADIUS, 128, 128);
const ioMaterial = new THREE.MeshStandardMaterial({
    map: ioTexture,
    roughness: 0.9,
    metalness: 0.1
});
const io = new THREE.Mesh(ioGeometry, ioMaterial);
// Positioned at ~125 degrees (Right-Back) to be fully lit
io.position.set(345000, 0, -242000);
io.castShadow = true;
io.receiveShadow = true;
scene.add(io);

const ioLabel = createLabel('Io');
ioLabel.scale.set(200000, 50000, 1); // 4:1 Ratio
ioLabel.position.copy(io.position);
ioLabel.position.y += IO_RADIUS + 10000; // Standardized to 10k km
scene.add(ioLabel);

// GANYMEDE
const ganymedeTexture = textureLoader.load('/textures/ganymede.png');
ganymedeTexture.anisotropy = renderer.capabilities.getMaxAnisotropy();
const ganymedeGeometry = new THREE.SphereGeometry(GANYMEDE_RADIUS, 128, 128);
const ganymedeMaterial = new THREE.MeshStandardMaterial({
    map: ganymedeTexture,
    roughness: 0.8,
    metalness: 0.1
});
const ganymede = new THREE.Mesh(ganymedeGeometry, ganymedeMaterial);
// Positioned at 1,070,400 km (True Distance)
// Visually between Europa and Jupiter (Background Left)
ganymede.position.set(-800000, 0, -710000);
ganymede.castShadow = true;
ganymede.receiveShadow = true;
scene.add(ganymede);

const ganymedeLabel = createLabel('Ganymede');
ganymedeLabel.scale.set(200000, 50000, 1); // 4:1 Ratio
ganymedeLabel.position.copy(ganymede.position);
ganymedeLabel.position.y += GANYMEDE_RADIUS + 10000; // Standardized to 10k km
scene.add(ganymedeLabel);

// CALLISTO
const callistoTexture = textureLoader.load('/textures/callisto.png');
callistoTexture.anisotropy = renderer.capabilities.getMaxAnisotropy();
const callistoGeometry = new THREE.SphereGeometry(CALLISTO_RADIUS, 128, 128);
const callistoMaterial = new THREE.MeshStandardMaterial({
    map: callistoTexture,
    roughness: 0.9, // Old, cratered surface
    metalness: 0.1
});
const callisto = new THREE.Mesh(callistoGeometry, callistoMaterial);
// Positioned at 1,882,700 km (True Distance)
// Positioned to appear at 90 degrees when at 600k altitude
callisto.position.set(1760000, 0, 670000);
callisto.castShadow = true;
callisto.receiveShadow = true;
scene.add(callisto);

const callistoLabel = createLabel('Callisto');
callistoLabel.scale.set(200000, 50000, 1); // 4:1 Ratio
callistoLabel.position.copy(callisto.position);
callistoLabel.position.y += CALLISTO_RADIUS + 10000;
scene.add(callistoLabel);

// EARTH
const earthTexture = textureLoader.load('/textures/earth.jpg');
earthTexture.anisotropy = renderer.capabilities.getMaxAnisotropy();
const earthGeometry = new THREE.SphereGeometry(EARTH_RADIUS, 128, 128);
const earthMaterial = new THREE.MeshStandardMaterial({
    map: earthTexture,
    roughness: 0.8,
    metalness: 0.0
});
const earth = new THREE.Mesh(earthGeometry, earthMaterial);
// Position at 1 AU from Sun (Side view relative to Jupiter line)
earth.position.copy(sunPosition).add(new THREE.Vector3(EARTH_DISTANCE, 0, 0));
earth.castShadow = true;
earth.receiveShadow = true;
earth.rotation.z = 23.5 * Math.PI / 180; // 23.5 degree Axial Tilt
scene.add(earth);

// MOON
const moonGeometry = new THREE.SphereGeometry(MOON_RADIUS, 64, 64);
const moonMaterial = new THREE.MeshStandardMaterial({
    map: callistoTexture, // Reusing Callisto texture for cratered look
    roughness: 0.9,
    metalness: 0.0
});
const moon = new THREE.Mesh(moonGeometry, moonMaterial);
// Position: 45 degrees "behind" Earth relative to Sun (Waxing Gibbous view)
moon.position.copy(earth.position).add(new THREE.Vector3(MOON_DISTANCE * 0.707, 0, MOON_DISTANCE * 0.707));
moon.castShadow = true;
moon.receiveShadow = true;
scene.add(moon);

const moonLabel = createLabel('Moon');
// Scale: similar to small moons
moonLabel.scale.set(100000, 25000, 1);
moonLabel.position.copy(moon.position);
moonLabel.position.y += MOON_RADIUS + 4500; // +4500km
scene.add(moonLabel);


// MERCURY
const mercuryTexture = textureLoader.load('/textures/mercury.jpg');
mercuryTexture.anisotropy = renderer.capabilities.getMaxAnisotropy();
const mercuryGeometry = new THREE.SphereGeometry(MERCURY_RADIUS, 128, 128);
const mercuryMaterial = new THREE.MeshStandardMaterial({
    map: mercuryTexture,
    roughness: 0.9,
    metalness: 0.0
});
const mercury = new THREE.Mesh(mercuryGeometry, mercuryMaterial);
// Position: 57.9M km from Sun (Negative X to separate from Earth)
mercury.position.copy(sunPosition).add(new THREE.Vector3(-MERCURY_DISTANCE, 0, 0));
mercury.castShadow = true;
mercury.receiveShadow = true;
mercury.receiveShadow = true;
scene.add(mercury);

// VENUS
const venusTexture = textureLoader.load('/textures/venus.jpg');
venusTexture.anisotropy = renderer.capabilities.getMaxAnisotropy();
const venusGeometry = new THREE.SphereGeometry(VENUS_RADIUS, 128, 128);
const venusMaterial = new THREE.MeshStandardMaterial({
    map: venusTexture,
    roughness: 0.9,
    metalness: 0.0
});
const venus = new THREE.Mesh(venusGeometry, venusMaterial);
// Position: 108.2M km from Sun (Use different axis to separate, e.g., -X but further)
venus.position.copy(sunPosition).add(new THREE.Vector3(-VENUS_DISTANCE, 0, 0));
venus.castShadow = true;
venus.receiveShadow = true;
scene.add(venus);

// MARS
const marsTexture = textureLoader.load('/textures/mars.jpg');
marsTexture.anisotropy = renderer.capabilities.getMaxAnisotropy();
const marsGeometry = new THREE.SphereGeometry(MARS_RADIUS, 128, 128);
const marsMaterial = new THREE.MeshStandardMaterial({
    map: marsTexture,
    roughness: 0.8,
    metalness: 0.0
});
const mars = new THREE.Mesh(marsGeometry, marsMaterial);
// Position: 227.9M km from Sun (+X axis to separate)
mars.position.copy(sunPosition).add(new THREE.Vector3(MARS_DISTANCE, 0, 0));
mars.castShadow = true;
mars.receiveShadow = true;
scene.add(mars);

// NEPTUNE (Texture: neptun.jpg)
const neptuneTexture = textureLoader.load('/textures/neptun.jpg');
neptuneTexture.anisotropy = renderer.capabilities.getMaxAnisotropy();
const neptuneGeometry = new THREE.SphereGeometry(NEPTUNE_RADIUS, 128, 128);
const neptuneMaterial = new THREE.MeshStandardMaterial({
    map: neptuneTexture,
    roughness: 0.9,
    metalness: 0.0
});
const neptune = new THREE.Mesh(neptuneGeometry, neptuneMaterial);
// Position: 4.5B km (+X axis but far out)
neptune.position.copy(sunPosition).add(new THREE.Vector3(NEPTUNE_DISTANCE, 0, 0));
neptune.castShadow = true;
neptune.receiveShadow = true;
scene.add(neptune);

// URANUS
const uranusTexture = textureLoader.load('/textures/uranus.jpg');
uranusTexture.anisotropy = renderer.capabilities.getMaxAnisotropy();
const uranusGeometry = new THREE.SphereGeometry(URANUS_RADIUS, 128, 128);
const uranusMaterial = new THREE.MeshStandardMaterial({
    map: uranusTexture,
    roughness: 1.0,
    metalness: 0.0
});
const uranus = new THREE.Mesh(uranusGeometry, uranusMaterial);
// Position: 2.87B km from Sun (Positive Z to separate)
uranus.position.copy(sunPosition).add(new THREE.Vector3(0, 0, URANUS_DISTANCE));
uranus.castShadow = true;
uranus.receiveShadow = true;
// AXIAL TILT: 98 degrees (Horizontal rotation)
// AXIAL TILT: 98 degrees (Horizontal rotation)
uranus.rotation.z = 98 * Math.PI / 180;
scene.add(uranus);

// SATURN & RINGS
const saturnTexture = textureLoader.load('/textures/saturn.jpg');
saturnTexture.anisotropy = renderer.capabilities.getMaxAnisotropy();
const saturnGeometry = new THREE.SphereGeometry(SATURN_RADIUS, 128, 128);
const saturnMaterial = new THREE.MeshStandardMaterial({
    map: saturnTexture,
    roughness: 1.0,
    metalness: 0.0
});
const saturn = new THREE.Mesh(saturnGeometry, saturnMaterial);
saturn.position.copy(sunPosition).add(new THREE.Vector3(0, 0, SATURN_DISTANCE)); // Positive Z like Uranus
saturn.rotation.z = SATURN_TILT; // 26.7 degree Tilt
saturn.rotation.x = -22 * Math.PI / 180; // Tilt opposite way (User Request)
saturn.castShadow = true;
saturn.receiveShadow = true;
scene.add(saturn);

// Saturn Rings
// Procedural Generation (User Request: Physically Accurate)
function createProceduralRingTexture() {
    const canvas = document.createElement('canvas');
    canvas.width = 1024;
    canvas.height = 1024;
    const ctx = canvas.getContext('2d');
    const cx = 512;
    const cy = 512;

    // Radius Mapping (Based on 5x Scale, Planet Radius ~205px)
    // D Ring: 228-253 (Faint)
    // C Ring: 253-312 (Transparent, Dark)
    // B Ring: 312-400 (Bright, Solid)
    // Cassini: 400-415 (Gap)
    // A Ring: 415-465 (Medium)

    for (let r = 228; r < 470; r++) {
        // Cassini Division (Sharp Gap)
        if (r > 400 && r < 415) continue;

        // Encke Gap (Tiny Detail)
        if (r > 453 && r < 455) continue;

        let hue = 38; // Saturnal Gold
        let sat = 30;
        let light = 50;
        let alpha = 1.0;

        // High Frequency Noise (Ringlets)
        const noise = Math.sin(r * 0.8) * Math.cos(r * 1.2) * Math.random();

        // Band Logic
        if (r < 253) {
            // D Ring
            light = 30;
            alpha = 0.05; // Ghostly
        } else if (r < 312) {
            // C Ring
            light = 35 + noise * 10;
            hue = 30; // Darker/Redder
            alpha = 0.4 + noise * 0.1;
        } else if (r <= 400) {
            // B Ring (Main)
            light = 70 + noise * 20; // Bright
            sat = 20;
            alpha = 0.9 + noise * 0.1;
        } else {
            // A Ring
            light = 60 + noise * 15;
            sat = 15;
            alpha = 0.7 + noise * 0.1;
        }

        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.strokeStyle = `hsl(${hue}, ${sat}%, ${light}%)`;
        ctx.globalAlpha = alpha;
        ctx.lineWidth = 1.5;
        ctx.stroke();
    }

    return new THREE.CanvasTexture(canvas);
}

const ringTexture = createProceduralRingTexture();

// Using PlaneGeometry with concentric texture
const ringPlaneGeometry = new THREE.PlaneGeometry(SATURN_RADIUS * 5, SATURN_RADIUS * 5);
const ringMaterial = new THREE.MeshStandardMaterial({
    map: ringTexture,
    transparent: true,
    side: THREE.DoubleSide,
    alphaTest: 0.01, // Keep faint rings
    roughness: 0.6,
    metalness: 0.1
});
const saturnRings = new THREE.Mesh(ringPlaneGeometry, ringMaterial);
saturnRings.rotation.x = -Math.PI / 2; // Flat relative to planet locally
// Add rings as child of Saturn to inherit Tilt
saturn.add(saturnRings);
moonLabel.position.y += MOON_RADIUS + 4500;
moonLabel.visible = false; // Hidden on start
scene.add(moonLabel);

// --- Camera & Animation ---

camera.position.set(0, CAMERA_HEIGHT, START_DISTANCE);
const euler = new THREE.Euler(0, 0, 0, 'YXZ');

const keys = { ArrowUp: false, ArrowDown: false, ArrowLeft: false, ArrowRight: false, Space: false };

window.addEventListener('keydown', (e) => {
    if (keys.hasOwnProperty(e.code)) keys[e.code] = true;

    // Teleport Keys 1-4 (Context Aware)
    if (['1', '2', '3', '4'].includes(e.key)) {
        const altitudes = { '1': 1000000, '2': 100000, '3': 10000, '4': 6000 };
        const alt = altitudes[e.key];

        if (currentLocation === Location.SOLAR_ORBIT) {
            // Teleport relative to SUN (Static view)
            // Position: SunPos + Z-offset (for comparison)
            camera.position.copy(sunPosition).add(new THREE.Vector3(0, 0, SUN_RADIUS + alt));
            camera.lookAt(sunPosition);
        } else if (currentLocation === Location.JUPITER_ORBIT) {
            // Teleport relative to JUPITER ( Descent view)
            camera.position.z = JUPITER_RADIUS + alt;
        } else if (currentLocation === Location.EARTH_ORBIT) {
            // Earth Teleport: Maintain current angle, just change distance
            const earthAlt = altitudes[e.key];
            const currentDir = new THREE.Vector3().subVectors(camera.position, earth.position).normalize();
            camera.position.copy(earth.position).add(currentDir.multiplyScalar(EARTH_RADIUS + earthAlt));
            camera.lookAt(earth.position);
        } else if (currentLocation === Location.MERCURY_ORBIT) {
            const dist = altitudes[e.key];
            const currentDir = new THREE.Vector3().subVectors(camera.position, mercury.position).normalize();
            camera.position.copy(mercury.position).add(currentDir.multiplyScalar(MERCURY_RADIUS + dist));
            camera.lookAt(mercury.position);
        } else if (currentLocation === Location.URANUS_ORBIT) {
            const dist = altitudes[e.key];
            const currentDir = new THREE.Vector3().subVectors(camera.position, uranus.position).normalize();
            camera.position.copy(uranus.position).add(currentDir.multiplyScalar(URANUS_RADIUS + dist));
            camera.lookAt(uranus.position);
            camera.position.copy(uranus.position).add(currentDir.multiplyScalar(URANUS_RADIUS + dist));
            camera.lookAt(uranus.position);
        } else if (currentLocation === Location.SATURN_ORBIT) {
            const dist = altitudes[e.key];
            const currentDir = new THREE.Vector3().subVectors(camera.position, saturn.position).normalize();
            camera.position.copy(saturn.position).add(currentDir.multiplyScalar(SATURN_RADIUS + dist));
            camera.lookAt(saturn.position);
        } else if (currentLocation === Location.VENUS_ORBIT) {
            const dist = altitudes[e.key];
            const currentDir = new THREE.Vector3().subVectors(camera.position, venus.position).normalize();
            camera.position.copy(venus.position).add(currentDir.multiplyScalar(VENUS_RADIUS + dist));
            camera.lookAt(venus.position);
        } else if (currentLocation === Location.MARS_ORBIT) {
            const dist = altitudes[e.key];
            const currentDir = new THREE.Vector3().subVectors(camera.position, mars.position).normalize();
            camera.position.copy(mars.position).add(currentDir.multiplyScalar(MARS_RADIUS + dist));
            camera.lookAt(mars.position);
        } else if (currentLocation === Location.NEPTUNE_ORBIT) {
            const dist = altitudes[e.key];
            const currentDir = new THREE.Vector3().subVectors(camera.position, neptune.position).normalize();
            camera.position.copy(neptune.position).add(currentDir.multiplyScalar(NEPTUNE_RADIUS + dist));
            camera.lookAt(neptune.position);
        } else {
            // From Moons: Return to Jupiter Orbit first
            returnToJupiter();
            camera.position.z = JUPITER_RADIUS + alt;
        }
    }

    // Key 5: Realistic Light Speed Travel
    if (e.key === '5') {
        // 1. Position at Start Point (100k km from Sun)
        currentLocation = Location.SOLAR_ORBIT; // Temporary state before launch

        // Reset Lighting (in case coming from Earth)
        if (typeof jupiter !== 'undefined') sunLight.target = jupiter;
        sunLight.intensity = 2.0;
        if (typeof moonLabel !== 'undefined' && moonLabel) moonLabel.visible = false;

        // Vector towards Jupiter (0,0,0) from Sun
        // Start 100,000 km from Sun Surface
        const startDist = SUN_RADIUS + 100000;
        const dirToJupiter = new THREE.Vector3().subVectors(new THREE.Vector3(0, 0, 0), sunPosition).normalize();

        camera.position.copy(sunPosition).add(dirToJupiter.multiplyScalar(startDist));
        camera.lookAt(0, 0, 0); // Face Jupiter
        euler.setFromQuaternion(camera.quaternion);

        // Hide UI
        if (sunLabel) sunLabel.visible = false;
        if (europaLabel) europaLabel.visible = false;
        if (callistoLabel) callistoLabel.visible = false;
        if (typeof moonLabel !== 'undefined' && moonLabel) moonLabel.visible = false;
        clearAtmosphereUI();

        // 2. Countdown Message
        const msg = document.getElementById('intro-message');
        if (msg) {
            msg.innerText = "LIGHT SPEED ENGAGING IN 10 SECONDS... DESTINATION: JUPITER. ESTIMATED TIME: 43 MINUTES.";
            msg.classList.remove('visible'); // Reset animation
            void msg.offsetWidth; // Trigger reflow
            msg.classList.add('visible');

            // Clear previous timeout if exists
            if (window.launchTimeout) clearTimeout(window.launchTimeout);

            // 3. Engage
            window.launchTimeout = setTimeout(() => {
                msg.classList.remove('visible');
                currentLocation = Location.LIGHT_SPEED_TRAVEL;
                flightTime = 0;
                nextMsgIndex = 0; // Reset message sequence

                // Show "Speed of Light" status
                const speedVal = document.getElementById('speed-value');
                if (speedVal) speedVal.innerText = "299,792 KM/S (c)";

            }, 10000); // 10s Delay
        }
    }

    // Key 6: Teleport to Earth
    if (e.key === '6') {
        if (window.launchTimeout) clearTimeout(window.launchTimeout);
        currentLocation = Location.EARTH_ORBIT;

        // Lighting: Aim Sun at Earth
        sunLight.target = earth;
        sunLight.intensity = 3.0; // Brighter at 1 AU

        // Position: 30,000 KM from Earth, angled view
        const dirToEarth = new THREE.Vector3().subVectors(camera.position, earth.position).normalize();

        // Offset camera to the "side" (30 degrees) to see the tilt and termination line
        // Move back 30,000km, then rotate that vector 30 degrees around Y axis
        const viewOffset = dirToEarth.clone().multiplyScalar(-30000).applyAxisAngle(new THREE.Vector3(0, 1, 0), 50 * Math.PI / 180);

        camera.position.copy(earth.position).add(viewOffset);
        camera.lookAt(earth.position);
        euler.setFromQuaternion(camera.quaternion);

        // Hide UI
        if (sunLabel) sunLabel.visible = false;
        if (europaLabel) europaLabel.visible = false;
        if (callistoLabel) callistoLabel.visible = false;
        if (typeof moonLabel !== 'undefined' && moonLabel) moonLabel.visible = true;
        clearAtmosphereUI();

        // Reset Message UI just in case
        const msg = document.getElementById('intro-message');
        if (msg) msg.classList.remove('visible');

        // Fact Sequence
        if (msg) {
            // Message 1
            setTimeout(() => {
                if (currentLocation === Location.EARTH_ORBIT) {
                    msg.innerText = "HOME. USE KEYS 1-4 TO COMPARE THIS SCALE WITH JUPITER AND THE SUN.";
                    msg.classList.add('visible');
                    setTimeout(() => { msg.classList.remove('visible'); }, 4000);
                }
            }, 3000);

            // Message 2
            setTimeout(() => {
                if (currentLocation === Location.EARTH_ORBIT) {
                    msg.innerText = "THE MOON ORBITS AT 384,400 KM. A DISTANCE THAT HAS HOSTED HUMANITY'S GREATEST VOYAGES.";
                    msg.classList.add('visible');
                    setTimeout(() => { msg.classList.remove('visible'); }, 4000);
                }
            }, 11000);

            // Message 3
            setTimeout(() => {
                if (currentLocation === Location.EARTH_ORBIT) {
                    msg.innerText = "A FRAGILE BLUE OASIS IN THE VASTNESS OF SPACE. THE ONLY KNOWN HARBOR FOR LIFE.";
                    msg.classList.add('visible');
                    setTimeout(() => { msg.classList.remove('visible'); }, 4000);
                }
            }, 19000);
        }
    }

    // Key 7: Teleport to Saturn
    if (e.key === '7') {
        if (window.launchTimeout) clearTimeout(window.launchTimeout);
        teleportToSaturn();
    }

    // Key 8: Teleport to Venus
    if (e.key === '8') {
        if (window.launchTimeout) clearTimeout(window.launchTimeout);
        teleportToVenus();
    }

    // Key 9: Teleport to Mars (New)
    if (e.key === '9') {
        if (window.launchTimeout) clearTimeout(window.launchTimeout);
        teleportToMars();
    }

    // Key 0: Teleport to Neptune
    if (e.key === '0') {
        if (window.launchTimeout) clearTimeout(window.launchTimeout);
        teleportToNeptune();
    }

    if (e.code === 'KeyJ') {
        returnToJupiter();
    }
});

window.addEventListener('keyup', (e) => {
    if (keys.hasOwnProperty(e.code)) keys[e.code] = false;
});

const clock = new THREE.Clock();
// State Management
const Location = {
    JUPITER_ORBIT: 'JUPITER_ORBIT',
    SOLAR_ORBIT: 'SOLAR_ORBIT',
    EUROPA_ORBIT: 'EUROPA_ORBIT',
    CALLISTO_ORBIT: 'CALLISTO_ORBIT',
    EARTH_ORBIT: 'EARTH_ORBIT',
    MERCURY_ORBIT: 'MERCURY_ORBIT',
    URANUS_ORBIT: 'URANUS_ORBIT',
    EARTH_ORBIT: 'EARTH_ORBIT',
    MERCURY_ORBIT: 'MERCURY_ORBIT',
    URANUS_ORBIT: 'URANUS_ORBIT',
    SATURN_ORBIT: 'SATURN_ORBIT',
    VENUS_ORBIT: 'VENUS_ORBIT',
    MARS_ORBIT: 'MARS_ORBIT',
    NEPTUNE_ORBIT: 'NEPTUNE_ORBIT',
    LIGHT_SPEED_TRAVEL: 'LIGHT_SPEED_TRAVEL'
};
let currentLocation = Location.JUPITER_ORBIT;
let atmosphereEntered = false;
let flightTime = 0;
let nextMsgIndex = 0;
const lightSpeedMessages = [
    { t: 10, text: "WE ARE TRAVELING AT 299,792 KM/S." },
    { t: 18, text: "LIGHT TAKES 8 MINUTES TO REACH EARTH FROM THE SUN." },
    { t: 26, text: "JUPITER IS 5 TIMES FARTHER AWAY FROM THE SUN THEN EARTH." },
    { t: 34, text: "WE WILL NEED 43 MINUTES TO ARRIVE, ALTHOUGH WE ARE TRAVELLING AT 300 THOUSAND KILOMETERS PER SECOND" },
    { t: 42, text: "YOU MIGHT BE SURPRISED HOW BORING THIS WILL BE." }
];

// Raycaster for Interaction
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();

window.addEventListener('mousemove', (event) => {
    // Calculate mouse position
    mouse.x = (event.clientX / window.innerWidth) * 2 - 1;
    mouse.y = -(event.clientY / window.innerHeight) * 2 + 1;

    raycaster.setFromCamera(mouse, camera);
    let intersects = [];

    // Raycast based on Location
    if (currentLocation === Location.JUPITER_ORBIT) {
        // Can click Sun, SunPoint, SunMesh, or Moons
        intersects = raycaster.intersectObjects([sunGlow, sunLabel, sunPoint, sunMesh, europa, europaLabel, callisto, callistoLabel]);
    } else if (currentLocation === Location.EUROPA_ORBIT) {
        // Can click Jupiter to return
        intersects = raycaster.intersectObject(jupiter);
    } else if (currentLocation === Location.CALLISTO_ORBIT) {
        // Can click Jupiter to return
        intersects = raycaster.intersectObject(jupiter);
    }

    if (intersects.length > 0) {
        document.body.style.cursor = 'pointer';

        // Hover Feedback
        const target = intersects[0].object;

        // Sun Hover
        if (currentLocation === Location.JUPITER_ORBIT && (target === sunGlow || target === sunLabel || target === sunPoint || target === sunMesh)) {
            if (sunLabel) {
                sunLabel.material.color.setHex(0xFFD700);
                sunLabel.scale.set(420000000, 105000000, 1);
            }
            if (sunPoint) {
                sunPoint.material.color.setHex(0xFFD700);
            }
        }
        // Europa Hover
        if (currentLocation === Location.JUPITER_ORBIT && (target === europa || target === europaLabel)) {
            if (europaLabel) {
                europaLabel.material.color.setHex(0xFFD700);
                europaLabel.scale.set(210000, 52500, 1);
            }
        }
        // Callisto Hover
        if (currentLocation === Location.JUPITER_ORBIT && (target === callisto || target === callistoLabel)) {
            if (callistoLabel) {
                callistoLabel.material.color.setHex(0xFFD700);
                callistoLabel.scale.set(210000, 52500, 1);
            }
        }
    } else {
        document.body.style.cursor = 'default';

        // Reset Labels
        if (currentLocation === Location.JUPITER_ORBIT && sunLabel) {
            sunLabel.material.color.setHex(0xFFFFFF);
            sunLabel.scale.set(400000000, 100000000, 1);
        }
        if (currentLocation === Location.JUPITER_ORBIT && sunPoint) {
            sunPoint.material.color.setHex(0xFFFFFF);
        }
        if (europaLabel) {
            europaLabel.material.color.setHex(0xFFFFFF);
            europaLabel.scale.set(200000, 50000, 1);
        }
        if (callistoLabel) {
            callistoLabel.material.color.setHex(0xFFFFFF);
            callistoLabel.scale.set(200000, 50000, 1);
        }
    }
});

window.addEventListener('click', () => {
    raycaster.setFromCamera(mouse, camera);

    if (currentLocation === Location.JUPITER_ORBIT) {

        // Check Sun Click
        let intersects = raycaster.intersectObjects([sunGlow, sunLabel]);
        if (intersects.length > 0) {
            currentLocation = Location.SOLAR_ORBIT;

            // Teleport to Sun
            const direction = new THREE.Vector3().subVectors(camera.position, sunPosition).normalize();
            camera.position.copy(sunPosition).add(direction.multiplyScalar(SUN_RADIUS + 1000000));
            camera.lookAt(sunPosition);
            euler.setFromQuaternion(camera.quaternion);

            // Toggle Visibility
            if (sunLabel) sunLabel.visible = false;
            if (europaLabel) europaLabel.visible = false;
            if (callistoLabel) callistoLabel.visible = false;
            // Context remains visible

            clearAtmosphereUI();
            return;
        }

        // Check Europa Click
        intersects = raycaster.intersectObjects([europa, europaLabel]);
        if (intersects.length > 0) {
            currentLocation = Location.EUROPA_ORBIT;

            // Teleport to Europa (100k km from surface)
            // Position camera between Europa and Jupiter to see Jupiter in background
            // Vector from Europa to Jupiter is towards origin (0,0,0) - EuropaPos
            const fromEuropaToJupiter = new THREE.Vector3().subVectors(new THREE.Vector3(0, 0, 0), europa.position).normalize();

            // Position = Europa + (VectorToJupiter * Distance)
            // Distance set to 50k km as per user request
            camera.position.copy(europa.position).add(fromEuropaToJupiter.multiplyScalar(EUROPA_RADIUS + 5000));

            // Look at Jupiter (0,0,0) so Europa is behind us
            camera.lookAt(0, 0, 0);
            euler.setFromQuaternion(camera.quaternion);

            // Toggle Visibility
            if (europaLabel) europaLabel.visible = false; // Hidden when close
            if (callistoLabel) callistoLabel.visible = false;

            clearAtmosphereUI();

            // Fact Sequence
            const msg = document.getElementById('intro-message');
            if (msg) {
                // Message 1
                setTimeout(() => {
                    if (currentLocation === Location.EUROPA_ORBIT) {
                        msg.innerText = "DISTANCE: 671,000 KM TO JUPITER. SECOND CLOSEST GALILEAN MOON.";
                        msg.classList.add('visible');
                        setTimeout(() => {
                            msg.classList.remove('visible');
                        }, 4000);
                    }
                }, 3000);

                // Message 2
                setTimeout(() => {
                    if (currentLocation === Location.EUROPA_ORBIT) {
                        msg.innerText = "RADIUS: 1,560 KM. SMALLEST GALILEAN MOON.";
                        msg.classList.add('visible');
                        setTimeout(() => {
                            msg.classList.remove('visible');
                        }, 4000);
                    }
                }, 11000);

                // Message 3
                setTimeout(() => {
                    if (currentLocation === Location.EUROPA_ORBIT) {
                        msg.innerText = "FAMOUS FOR ITS SUBSURFACE OCEAN AND LACK OF CRATERS.";
                        msg.classList.add('visible');
                        setTimeout(() => {
                            msg.classList.remove('visible');
                        }, 4000);
                    }
                }, 19000);

                // Message 4
                setTimeout(() => {
                    if (currentLocation === Location.EUROPA_ORBIT) {
                        msg.innerText = "THE EUROPA CLIPPER MISSION, WHICH AIMS TO SPECIFICALLY ANALYZE THE MOON, WILL ARRIVE IN 2030.";
                        msg.classList.add('visible');
                        setTimeout(() => {
                            msg.classList.remove('visible');
                        }, 4000);
                    }
                }, 27000);
            }

            return;
        }

        // Check Callisto Click
        intersects = raycaster.intersectObjects([callisto, callistoLabel]);
        if (intersects.length > 0) {
            currentLocation = Location.CALLISTO_ORBIT;

            // Teleport to Callisto (Side/Sunny Side)
            // Vector from Callisto to Sun
            const toSun = new THREE.Vector3().subVectors(sunPosition, callisto.position).normalize();

            // Rotate 60 degrees to the side to see Jupiter past Callisto
            toSun.applyAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 3);

            // Position = Callisto + (VectorToSun * Distance) (5000km altitude)
            camera.position.copy(callisto.position).add(toSun.multiplyScalar(CALLISTO_RADIUS + 5000));

            // Look at Callisto
            camera.lookAt(callisto.position);
            euler.setFromQuaternion(camera.quaternion);

            if (callistoLabel) callistoLabel.visible = false;
            if (europaLabel) europaLabel.visible = false;

            clearAtmosphereUI();

            // Fact Sequence
            const msg = document.getElementById('intro-message');
            if (msg) {
                // Message 1 (Start at 3s, Duration 4s)
                setTimeout(() => {
                    if (currentLocation === Location.CALLISTO_ORBIT) {
                        msg.innerText = "DISTANCE: 1.88 MILLION KM TO JUPITER. THE FARTHEST GALILEAN MOON.";
                        msg.classList.add('visible');
                        setTimeout(() => {
                            msg.classList.remove('visible');
                        }, 4000);
                    }
                }, 3000);

                // Message 2 (Start at 3s + 4s + 4s = 11s, Duration 4s)
                setTimeout(() => {
                    if (currentLocation === Location.CALLISTO_ORBIT) {
                        msg.innerText = "RADIUS: 2,410 KM. SECOND LARGEST MOON, ONLY SLIGHTLY SMALLER THAN GANYMEDE.";
                        msg.classList.add('visible');
                        setTimeout(() => {
                            msg.classList.remove('visible');
                        }, 4000);
                    }
                }, 11000);
            }

            return;
        }



    } else if (currentLocation === Location.EUROPA_ORBIT) {
        // Return from Europa (Click Jupiter)
        const intersects = raycaster.intersectObject(jupiter);
        if (intersects.length > 0) {
            returnToJupiter();
        }
    } else if (currentLocation === Location.CALLISTO_ORBIT) {
        // Return from Callisto (Click Jupiter)
        const intersects = raycaster.intersectObject(jupiter);
        if (intersects.length > 0) {
            returnToJupiter();
        }
    }
});

function clearAtmosphereUI() {
    scene.fog.density = 0;
    renderer.domElement.style.filter = 'none';
    const ids = ['signal-lost-overlay', 'signal-lost-message', 'pressure-warning', 'atmosphere-notification'];
    ids.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.remove('visible');
    });
    atmosphereEntered = false;
}

function returnToJupiter() {
    currentLocation = Location.JUPITER_ORBIT;

    // Reset Lighting to Jupiter
    if (typeof jupiter !== 'undefined') sunLight.target = jupiter;
    sunLight.intensity = 3.0; // Standardized High Scale
    if (typeof moonLabel !== 'undefined' && moonLabel) moonLabel.visible = false;

    // Reset Camera
    camera.position.set(0, CAMERA_HEIGHT, START_DISTANCE);
    camera.lookAt(0, 0, 0);

    // Reset Visibility
    if (sunLabel) {
        sunLabel.visible = true;
        sunLabel.scale.set(400000000, 100000000, 1);
    }

    // Standardize Sun Visuals (Physical + Minimum Point)
    if (typeof sunGlow !== 'undefined' && sunGlow) sunGlow.visible = false;
    if (typeof sunPoint !== 'undefined' && sunPoint) sunPoint.visible = true;
    if (typeof sunMesh !== 'undefined' && sunMesh) sunMesh.visible = true;

    if (europaLabel) europaLabel.visible = true;
    if (callistoLabel) callistoLabel.visible = true;

    // Reset Stars
    if (starField) starField.position.set(0, 0, 0);
    if (smallStars) smallStars.position.set(0, 0, 0);

    clearAtmosphereUI();

    // Reset Camera Rotation
    euler.set(0, 0, 0);
    camera.quaternion.setFromEuler(euler);
}

function animate() {
    requestAnimationFrame(animate);
    const delta = clock.getDelta();

    if (currentLocation === Location.JUPITER_ORBIT) {
        // Descent Logic
        const currentSpeed = keys.Space ? WARP_SPEED : DESCENT_SPEED;

        // Stop if we hit the surface (Stop Distance)
        if (camera.position.z > STOP_DISTANCE) {
            camera.position.z -= currentSpeed * delta;
        }

        // Camera Rotation (Look around)
        const rotSpeed = 0.5 * delta;
        if (keys.ArrowUp) euler.x += rotSpeed;
        if (keys.ArrowDown) euler.x -= rotSpeed;
        if (keys.ArrowLeft) euler.y += rotSpeed;
        if (keys.ArrowRight) euler.y -= rotSpeed;

        euler.x = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, euler.x));
        camera.quaternion.setFromEuler(euler);

        // Update UI
        const altitude = Math.max(0, Math.floor(camera.position.z - JUPITER_RADIUS));
        const altitudeEl = document.getElementById('altitude-value');
        const speedEl = document.getElementById('speed-value');

        if (altitudeEl) altitudeEl.innerText = altitude.toLocaleString() + " KM";
        if (speedEl) speedEl.innerText = currentSpeed.toLocaleString() + " KM/S";

        // Atmospheric Effects
        const currentAlt = camera.position.z - JUPITER_RADIUS;

        // 1. Atmosphere Notification
        const atmosphereNotification = document.getElementById('atmosphere-notification');
        if (currentAlt < 4000 && !atmosphereEntered) {
            atmosphereEntered = true;
            if (atmosphereNotification) {
                atmosphereNotification.classList.add('visible');
                setTimeout(() => {
                    atmosphereNotification.classList.remove('visible');
                }, 4000);
            }
        }

        // 2. Atmospheric Blur & Fog
        if (currentAlt < 4000) {
            // Blur increases from 0px at 4000km to 10px at 0km
            const blurAmount = Math.max(0, (4000 - currentAlt) / 4000) * 10;
            renderer.domElement.style.filter = `blur(${blurAmount}px)`;
            // Linear fog density increase
            scene.fog.density = (1 - (currentAlt / 4000)) * 0.002;
        } else {
            renderer.domElement.style.filter = 'none';
            scene.fog.density = 0;
        }

        // 3. Pressure Warning & Shake
        const pressureWarning = document.getElementById('pressure-warning');
        if (currentAlt < 2000) {
            if (pressureWarning) pressureWarning.classList.add('visible');

            // Calculate Shake Intensity (0 to 1)
            // Starts at 2000km (0%) and reaches 100% at 0km
            const shakeIntensity = Math.max(0, (2000 - currentAlt) / 2000);

            // Max shake deviation in km. Increased to 200 for visibility
            const maxShake = 200;

            // Apply Jitter (Resetting base position each frame to avoid drift)
            camera.position.x = (Math.random() - 0.5) * maxShake * shakeIntensity;
            camera.position.y = CAMERA_HEIGHT + (Math.random() - 0.5) * maxShake * shakeIntensity;
        } else {
            if (pressureWarning) pressureWarning.classList.remove('visible');
            camera.position.x = 0;
            // Always maintain base height when not shaking
            if (Math.abs(camera.position.y - CAMERA_HEIGHT) > 100) camera.position.y = CAMERA_HEIGHT;
        }

        // 4. Signal Lost
        if (currentAlt <= 50) {
            const overlay = document.getElementById('signal-lost-overlay');
            const message = document.getElementById('signal-lost-message');
            if (overlay) overlay.classList.add('visible');
            if (message) message.classList.add('visible');
        }

    } else if (currentLocation === Location.LIGHT_SPEED_TRAVEL) {
        // Move towards Jupiter at Light Speed
        flightTime += delta;
        const speed = SPEED_OF_LIGHT * delta;

        // Dynamic Altitude (Distance from Sun)
        const altitudeEl = document.getElementById('altitude-value');
        if (altitudeEl) {
            const dist = camera.position.distanceTo(sunPosition) - SUN_RADIUS;
            altitudeEl.innerText = Math.floor(dist).toLocaleString() + " KM (FROM SUN)";
        }

        // Timed Messages
        // Helper to show message
        const showMsg = (text) => {
            const msg = document.getElementById('intro-message');
            if (msg) {
                msg.innerText = text;
                msg.classList.add('visible');
                setTimeout(() => msg.classList.remove('visible'), 3000);
            }
        };

        // Schedule
        // Schedule (Robust)
        if (nextMsgIndex < lightSpeedMessages.length) {
            const nextMsg = lightSpeedMessages[nextMsgIndex];
            if (flightTime >= nextMsg.t) {
                showMsg(nextMsg.text);
                nextMsgIndex++;
            }
        }


        // Move towards 0,0,0
        const dir = new THREE.Vector3().subVectors(new THREE.Vector3(0, 0, 0), camera.position).normalize();
        camera.position.add(dir.multiplyScalar(speed));

        // Camera Rotation (Look around)
        const rotSpeed = 0.5 * delta;
        if (keys.ArrowUp) euler.x += rotSpeed;
        if (keys.ArrowDown) euler.x -= rotSpeed;
        if (keys.ArrowLeft) euler.y += rotSpeed;
        if (keys.ArrowRight) euler.y -= rotSpeed;

        euler.x = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, euler.x));
        camera.quaternion.setFromEuler(euler);

        // Check Arrival (Standard Start Distance: 1M km altitude)
        const distToCenter = camera.position.length();
        if (distToCenter <= START_DISTANCE) {
            // Arrival!
            returnToJupiter();

            // Reset Speed display
            const speedVal = document.getElementById('speed-value');
            if (speedVal) speedVal.innerText = "25 KM/S";

            // Arrival Message
            const msg = document.getElementById('intro-message');
            if (msg) {
                msg.innerText = "ARRIVED AT JUPITER ORBIT.";
                msg.classList.add('visible');
                setTimeout(() => msg.classList.remove('visible'), 4000);
            }
        }
    } else {
        // Solar or Europa Orbit (Dynamic Altitude)
        const altitudeEl = document.getElementById('altitude-value');
        const sunDistEl = document.getElementById('sun-dist-value');
        const speedEl = document.getElementById('speed-value');

        // Always update Sun Distance
        if (sunDistEl) {
            const distToSun = camera.position.distanceTo(sunPosition) - SUN_RADIUS;
            sunDistEl.innerText = Math.floor(distToSun).toLocaleString() + " KM";
        }

        if (currentLocation === Location.SOLAR_ORBIT) {
            const dist = camera.position.distanceTo(sunPosition) - SUN_RADIUS;
            if (altitudeEl) altitudeEl.innerText = Math.floor(dist).toLocaleString() + " KM";
        } else if (currentLocation === Location.EUROPA_ORBIT) {
            const dist = camera.position.distanceTo(europa.position) - EUROPA_RADIUS;
            if (altitudeEl) altitudeEl.innerText = Math.floor(dist).toLocaleString() + " KM";
        } else if (currentLocation === Location.CALLISTO_ORBIT) {
            const dist = camera.position.distanceTo(callisto.position) - CALLISTO_RADIUS;
            if (altitudeEl) altitudeEl.innerText = Math.floor(dist).toLocaleString() + " KM";
        } else if (currentLocation === Location.EARTH_ORBIT) {
            const dist = camera.position.distanceTo(earth.position) - EARTH_RADIUS;
            if (altitudeEl) altitudeEl.innerText = Math.floor(dist).toLocaleString() + " KM";
        } else if (currentLocation === Location.MERCURY_ORBIT) {
            const dist = camera.position.distanceTo(mercury.position) - MERCURY_RADIUS;
            if (altitudeEl) altitudeEl.innerText = Math.floor(dist).toLocaleString() + " KM";
        } else if (currentLocation === Location.URANUS_ORBIT) {
            const dist = camera.position.distanceTo(uranus.position) - URANUS_RADIUS;
            if (altitudeEl) altitudeEl.innerText = Math.floor(dist).toLocaleString() + " KM";
        } else if (currentLocation === Location.URANUS_ORBIT) {
            const dist = camera.position.distanceTo(uranus.position) - URANUS_RADIUS;
            if (altitudeEl) altitudeEl.innerText = Math.floor(dist).toLocaleString() + " KM";
        } else if (currentLocation === Location.SATURN_ORBIT) {
            const dist = camera.position.distanceTo(saturn.position) - SATURN_RADIUS;
            if (altitudeEl) altitudeEl.innerText = Math.floor(dist).toLocaleString() + " KM";
        } else if (currentLocation === Location.VENUS_ORBIT) {
            const dist = camera.position.distanceTo(venus.position) - VENUS_RADIUS;
            if (altitudeEl) altitudeEl.innerText = Math.floor(dist).toLocaleString() + " KM";
        } else if (currentLocation === Location.MARS_ORBIT) {
            const dist = camera.position.distanceTo(mars.position) - MARS_RADIUS;
            if (altitudeEl) altitudeEl.innerText = Math.floor(dist).toLocaleString() + " KM";
        } else if (currentLocation === Location.NEPTUNE_ORBIT) {
            const dist = camera.position.distanceTo(neptune.position) - NEPTUNE_RADIUS;
            if (altitudeEl) altitudeEl.innerText = Math.floor(dist).toLocaleString() + " KM";
        } else {
            if (altitudeEl) altitudeEl.innerText = "ORBIT STABLE";
        }

        if (speedEl) speedEl.innerText = "0 KM/S";

        // Still allow looking around
        const rotSpeed = 0.5 * delta;
        if (keys.ArrowUp) euler.x += rotSpeed;
        if (keys.ArrowDown) euler.x -= rotSpeed;
        if (keys.ArrowLeft) euler.y += rotSpeed;
        if (keys.ArrowRight) euler.y -= rotSpeed;
        euler.x = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, euler.x));
        camera.quaternion.setFromEuler(euler);
    }

    const warpNotification = document.getElementById('warp-notification');
    if (warpNotification) {
        if (keys.Space && currentLocation === Location.JUPITER_ORBIT) {
            warpNotification.classList.add('visible');
        } else {
            warpNotification.classList.remove('visible');
        }
    }

    // Global HUD Updates
    const sunDistEl = document.getElementById('sun-dist-value');
    if (sunDistEl) {
        // Calculate distance to Sun Surface
        const distToSun = camera.position.distanceTo(sunPosition) - SUN_RADIUS;
        sunDistEl.innerText = Math.floor(distToSun).toLocaleString() + " KM";
    }



    renderer.render(scene, camera);
}

// Resize Handler
window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
});

// Intro Message Logic
setTimeout(() => {
    const msg = document.getElementById('intro-message');
    if (msg) {
        msg.classList.add('visible');
        setTimeout(() => {
            msg.classList.remove('visible');
        }, 6000); // Stay for 6 seconds
    }
}, 3000); // Start after 3 seconds

// Info Icon Logic
const infoIcon = document.getElementById('info-icon');
const infoBox = document.getElementById('info-box');
const infoBoxClose = document.getElementById('info-box-close');

if (infoIcon && infoBox) {
    infoIcon.addEventListener('click', () => {
        infoBox.classList.toggle('visible');
    });
}

if (infoBoxClose && infoBox) {
    infoBoxClose.addEventListener('click', () => {
        infoBox.classList.remove('visible');
    });
}

// About Icon Logic
const aboutIcon = document.getElementById('about-icon');
const aboutBox = document.getElementById('about-box');
const aboutBoxClose = document.getElementById('about-box-close');

if (aboutIcon && aboutBox) {
    aboutIcon.addEventListener('click', () => {
        aboutBox.classList.toggle('visible');
    });
}

if (aboutBoxClose && aboutBox) {
    aboutBoxClose.addEventListener('click', () => {
        aboutBox.classList.remove('visible');
    });
}

animate();

// Navigation Menu Logic
const navIcon = document.getElementById('nav-icon');
const navContainer = document.getElementById('nav-container');

if (navIcon && navContainer) {
    navIcon.addEventListener('click', () => {
        navContainer.classList.toggle('visible');
    });
}

document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
        const target = e.target.dataset.target;

        switch (target) {
            case 'JUPITER':
                returnToJupiter();
                break;
            case 'EARTH':
                teleportToEarth();
                break;
            case 'SUN':
                teleportToSun();
                break;
            case 'MERCURY':
                teleportToMercury();
                break;
            case 'URANUS':
                teleportToUranus();
                break;
            case 'SATURN':
                teleportToSaturn();
                break;
            case 'VENUS':
                teleportToVenus();
                break;
            case 'MARS':
                teleportToMars();
                break;
            case 'NEPTUNE':
                teleportToNeptune();
                break;
            default:
                displayNavMessage("DESTINATION " + target + " LOCKED. CALCULATING TRAJECTORY...");
        }
    });
});

function displayNavMessage(text) {
    const msg = document.getElementById('intro-message');
    if (msg) {
        msg.innerText = text;
        msg.classList.add('visible');
        setTimeout(() => msg.classList.remove('visible'), 3000);
    }
}

function teleportToSun() {
    currentLocation = Location.SOLAR_ORBIT;

    // Position: 100,000 km from Sun Surface
    camera.position.copy(sunPosition);
    camera.position.z += (SUN_RADIUS + 100000);
    camera.lookAt(sunPosition);

    // Reset Physics/UI
    euler.set(0, 0, 0);
    camera.quaternion.setFromEuler(euler);

    if (typeof moonLabel !== 'undefined' && moonLabel) moonLabel.visible = false;
    if (typeof sunLabel !== 'undefined' && sunLabel) sunLabel.visible = false;

    // Standardize Sun Visuals (Physical + Minimum Point)
    if (typeof sunGlow !== 'undefined' && sunGlow) sunGlow.visible = false;
    if (typeof sunPoint !== 'undefined' && sunPoint) sunPoint.visible = true;
    if (typeof sunMesh !== 'undefined' && sunMesh) sunMesh.visible = true;

    sunLight.intensity = 3.0;

    displayNavMessage("WELCOME TO THE SUN.");
}

function teleportToEarth() {
    currentLocation = Location.EARTH_ORBIT;

    // Cinematic View (Matching Key 6)
    // Position: 30,000 KM from Earth, angled view
    const dirToEarth = new THREE.Vector3().subVectors(earth.position, sunPosition).normalize();

    // Offset camera to the "side" (30 degrees) to see the tilt and termination line
    // Move back 30,000km, then rotate that vector 50 degrees around Y axis
    const viewOffset = dirToEarth.clone().multiplyScalar(-30000).applyAxisAngle(new THREE.Vector3(0, 1, 0), 50 * Math.PI / 180);

    camera.position.copy(earth.position).add(viewOffset);
    camera.lookAt(earth.position);

    // Lighting
    sunLight.intensity = 3.0;
    sunLight.target = earth;

    // UI
    if (typeof moonLabel !== 'undefined' && moonLabel) moonLabel.visible = true;

    // Standardize Sun Visuals (Physical + Minimum Point)
    if (typeof sunGlow !== 'undefined' && sunGlow) sunGlow.visible = false;
    if (typeof sunPoint !== 'undefined' && sunPoint) sunPoint.visible = true;
    if (typeof sunMesh !== 'undefined' && sunMesh) sunMesh.visible = true;

    // Reset Rotation
    euler.set(0, 0, 0);
    camera.quaternion.setFromEuler(euler);

    // Trigger Message Sequence
    const msg = document.getElementById('intro-message');
    if (msg) {
        msg.innerText = "ORBITING EARTH.";
        msg.classList.add('visible');
        setTimeout(() => msg.classList.remove('visible'), 4000);

        setTimeout(() => {
            if (currentLocation === Location.EARTH_ORBIT) {
                msg.innerText = "A FRAGILE BLUE OASIS IN THE VASTNESS OF SPACE.";
                msg.classList.add('visible');
                setTimeout(() => msg.classList.remove('visible'), 6000);
            }
        }, 5000);
    }
}

function teleportToMercury() {
    currentLocation = Location.MERCURY_ORBIT;
    // 100k km from surface
    const dirToMercury = new THREE.Vector3().subVectors(mercury.position, sunPosition).normalize();

    // User Request: 40-degree offset for phase view
    const viewDir = dirToMercury.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), 40 * Math.PI / 180);

    // Position on Sun Side with offset
    camera.position.copy(mercury.position).sub(viewDir.multiplyScalar(MERCURY_RADIUS + 100000));
    camera.lookAt(mercury.position);
    euler.setFromQuaternion(camera.quaternion);

    if (typeof moonLabel !== 'undefined' && moonLabel) moonLabel.visible = false;
    // Standardize Sun Visuals (Physical + Minimum Point)
    if (typeof sunGlow !== 'undefined' && sunGlow) sunGlow.visible = false;
    if (typeof sunPoint !== 'undefined' && sunPoint) sunPoint.visible = true;
    if (typeof sunMesh !== 'undefined' && sunMesh) sunMesh.visible = true;

    if (typeof sunLabel !== 'undefined' && sunLabel) {
        sunLabel.visible = true;
        // Scale down for close view (approx 1/13th of Jupiter view)
        sunLabel.scale.set(30000000, 7500000, 1);
    }
    sunLight.intensity = 3.0;
    sunLight.target = mercury;

    displayNavMessage("ORBITING MERCURY.");
}

function teleportToUranus() {
    currentLocation = Location.URANUS_ORBIT;
    // 100k km
    const dirToUranus = new THREE.Vector3().subVectors(uranus.position, sunPosition).normalize();

    // User Request: 40-degree offset for phase view
    const viewDir = dirToUranus.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), 40 * Math.PI / 180);

    camera.position.copy(uranus.position).sub(viewDir.multiplyScalar(URANUS_RADIUS + 100000));
    camera.lookAt(uranus.position);
    euler.setFromQuaternion(camera.quaternion);

    if (typeof moonLabel !== 'undefined' && moonLabel) moonLabel.visible = false;

    // Hide Sun Label (User Request)
    if (typeof sunLabel !== 'undefined' && sunLabel) {
        sunLabel.visible = false;
    }

    // Standardize Sun Visuals (Physical + Minimum Point)
    if (typeof sunGlow !== 'undefined' && sunGlow) sunGlow.visible = false;
    if (typeof sunPoint !== 'undefined' && sunPoint) sunPoint.visible = true;
    if (typeof sunMesh !== 'undefined' && sunMesh) sunMesh.visible = true;

    sunLight.intensity = 3.0; // Max brightness
    sunLight.target = uranus;

    displayNavMessage("ORBITING URANUS.");
}

function teleportToSaturn() {
    currentLocation = Location.SATURN_ORBIT;

    // Position: 100k km from Surface
    // Vector similar to Uranus/Jupiter (Side/Front view)
    const dirToSaturn = new THREE.Vector3().subVectors(saturn.position, sunPosition).normalize();

    // Position camera on the sun-lit side but angled to see rings well (e.g., slightly above equator)
    // Saturn at saturn.position. Sun at sunPosition.
    // Move towards sun by (Radius + 100k), then move UP (Y) to see rings?
    // Saturn has Z-tilt of 26deg. Rings are in XY plane of Saturn (which is tilted).
    // Let's position camera roughly "in front" (towards sun) but offset.

    const dist = SATURN_RADIUS + 100000;
    const viewPos = saturn.position.clone().sub(dirToSaturn.multiplyScalar(dist));
    viewPos.y += 30000; // Look down slightly on rings

    camera.position.copy(viewPos);
    camera.lookAt(saturn.position);
    euler.setFromQuaternion(camera.quaternion);

    if (typeof moonLabel !== 'undefined' && moonLabel) moonLabel.visible = false;

    // Hide Sun Label (User Request)
    if (typeof sunLabel !== 'undefined' && sunLabel) {
        sunLabel.visible = false;
    }

    // Standardize Sun Visuals (Physical + Minimum Point)
    if (typeof sunGlow !== 'undefined' && sunGlow) sunGlow.visible = false;
    if (typeof sunPoint !== 'undefined' && sunPoint) sunPoint.visible = true;
    if (typeof sunMesh !== 'undefined' && sunMesh) sunMesh.visible = true;

    sunLight.intensity = 3.0; // Max brightness
    sunLight.target = saturn;

    displayNavMessage("ORBITING SATURN.");
}

function teleportToVenus() {
    currentLocation = Location.VENUS_ORBIT;

    // Position: 20k km from surface (Close view for clouds)
    const dirToVenus = new THREE.Vector3().subVectors(venus.position, sunPosition).normalize();

    // User Request: "Not directly between, but side view to see shape"
    // Rotate the "sun-to-venus" vector by ~40 degrees to the side
    // Then back off from Venus along that new angle.
    const viewDir = dirToVenus.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), 40 * Math.PI / 180);

    // Position camera
    camera.position.copy(venus.position).sub(viewDir.multiplyScalar(VENUS_RADIUS + 20000));
    camera.lookAt(venus.position);
    euler.setFromQuaternion(camera.quaternion);

    if (typeof moonLabel !== 'undefined' && moonLabel) moonLabel.visible = false;

    // Hide Sun Label (User Request)
    if (typeof sunLabel !== 'undefined' && sunLabel) {
        sunLabel.visible = false;
    }

    // Standardize Sun Visuals (Physical + Minimum Point)
    // VENUS IS INNER PLANET (0.7 AU) - Show Glow for brightness/heat feel?
    // Plan Decision: Use Sun Glow because it's an inner planet and very bright.
    if (typeof sunGlow !== 'undefined' && sunGlow) {
        sunGlow.visible = true;
        // Scale appropriately for this distance?
        sunGlow.scale.set(SUN_RADIUS * 6, SUN_RADIUS * 6, 1);
    }
    if (typeof sunPoint !== 'undefined' && sunPoint) sunPoint.visible = false;
    if (typeof sunMesh !== 'undefined' && sunMesh) sunMesh.visible = true;

    sunLight.intensity = 3.0; // Max brightness
    sunLight.target = venus;

    displayNavMessage("ORBITING VENUS.");
}

function teleportToMars() {
    currentLocation = Location.MARS_ORBIT;

    // Position: 10,000 km from surface (Close view)
    const dirToMars = new THREE.Vector3().subVectors(mars.position, sunPosition).normalize();

    // User Request: 40-degree offset for phase view
    const viewDir = dirToMars.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), 40 * Math.PI / 180);

    camera.position.copy(mars.position).sub(viewDir.multiplyScalar(MARS_RADIUS + 10000));
    camera.lookAt(mars.position);
    euler.setFromQuaternion(camera.quaternion);

    if (typeof moonLabel !== 'undefined' && moonLabel) moonLabel.visible = false;
    if (typeof sunLabel !== 'undefined' && sunLabel) sunLabel.visible = false;

    // Standardize Sun Visuals
    // MARS (1.5 AU) - Use Sun Point (Distance > 1 AU)
    if (typeof sunGlow !== 'undefined' && sunGlow) sunGlow.visible = false;
    if (typeof sunPoint !== 'undefined' && sunPoint) sunPoint.visible = true;
    if (typeof sunMesh !== 'undefined' && sunMesh) sunMesh.visible = true;

    sunLight.intensity = 3.0; // Max brightness
    sunLight.target = mars;

    displayNavMessage("ORBITING MARS.");
}

function teleportToNeptune() {
    currentLocation = Location.NEPTUNE_ORBIT;

    // Position: 100k km from surface
    const dirToNeptune = new THREE.Vector3().subVectors(neptune.position, sunPosition).normalize();

    // User Request: 40-degree offset for phase view
    const viewDir = dirToNeptune.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), 40 * Math.PI / 180);

    camera.position.copy(neptune.position).sub(viewDir.multiplyScalar(NEPTUNE_RADIUS + 100000));
    camera.lookAt(neptune.position);
    euler.setFromQuaternion(camera.quaternion);

    if (typeof moonLabel !== 'undefined' && moonLabel) moonLabel.visible = false;
    if (typeof sunLabel !== 'undefined' && sunLabel) sunLabel.visible = false;

    // Standardize Sun Visuals
    // NEPTUNE (30 AU) - Point Only
    if (typeof sunGlow !== 'undefined' && sunGlow) sunGlow.visible = false;
    if (typeof sunPoint !== 'undefined' && sunPoint) sunPoint.visible = true;
    if (typeof sunMesh !== 'undefined' && sunMesh) sunMesh.visible = true;

    sunLight.intensity = 3.0; // Max brightness
    sunLight.target = neptune;

    displayNavMessage("ORBITING NEPTUNE.");
}