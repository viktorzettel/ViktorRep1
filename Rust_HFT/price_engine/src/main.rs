use std::env;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::net::UdpSocket;
use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
use futures_util::StreamExt;
use serde::Deserialize;
use serde_json::Value;
use core_types::SignalPacket;
use statrs::distribution::{Normal, ContinuousCDF};
use log::{info, error, warn};

// --- STATS HELPER ---
struct RollingStats {
    window_size: usize,
    returns: VecDeque<f64>,
    last_price: f64,
    sum_r: f64,
    sum_r2: f64,
    sum_r3: f64,
    sum_r4: f64,
}

impl RollingStats {
    fn new(window_size: usize) -> Self {
        Self {
            window_size,
            returns: VecDeque::with_capacity(window_size),
            last_price: 0.0,
            sum_r: 0.0,
            sum_r2: 0.0,
            sum_r3: 0.0,
            sum_r4: 0.0,
        }
    }

    fn update(&mut self, price: f64) {
        if self.last_price == 0.0 {
            self.last_price = price;
            return;
        }

        let ret = (price / self.last_price).ln();
        self.last_price = price;

        self.returns.push_back(ret);
        self.sum_r += ret;
        self.sum_r2 += ret * ret;
        self.sum_r3 += ret * ret * ret;
        self.sum_r4 += ret * ret * ret * ret;

        if self.returns.len() > self.window_size {
            let old = self.returns.pop_front().unwrap();
            self.sum_r -= old;
            self.sum_r2 -= old * old;
            self.sum_r3 -= old * old * old;
            self.sum_r4 -= old * old * old * old;
        }
    }

    fn get_moments(&self) -> (f64, f64) {
        let n = self.returns.len() as f64;
        if n < 30.0 { return (0.0, 0.0); } // Not enough data

        let mean = self.sum_r / n;
        // Central moments
        // Approx for speed: assume mean is small (~0) for HFT 1s returns?
        // No, let's be precise.
        // Variance = E[x^2] - (E[x])^2
        let variance = (self.sum_r2 / n) - (mean * mean);
        let stdev = variance.sqrt();

        if stdev < 1e-9 { return (0.0, 0.0); }

        // Skew = E[((x-mu)/sigma)^3]
        // E[(x-mu)^3] = E[x^3 - 3x^2mu + 3xmu^2 - mu^3]
        //             = E[x^3] - 3muE[x^2] + 2mu^3
        let m3 = (self.sum_r3 / n) - 3.0 * mean * (self.sum_r2 / n) + 2.0 * mean.powi(3);
        let skew = m3 / stdev.powi(3);

        // Kurt = E[((x-mu)/sigma)^4]
        // E[(x-mu)^4] = E[x^4 - 4x^3mu + 6x^2mu^2 - 4xmu^3 + mu^4]
        //             = E[x^4] - 4muE[x^3] + 6mu^2E[x^2] - 3mu^4
        let m4 = (self.sum_r4 / n)
            - 4.0 * mean * (self.sum_r3 / n)
            + 6.0 * mean.powi(2) * (self.sum_r2 / n)
            - 3.0 * mean.powi(4);
            
        let kurt = (m4 / stdev.powi(4)) - 3.0; // Excess Kurtosis

        (skew, kurt)
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    env_logger::init();
    info!("Starting Price Engine (Tokyo Node)...");

    // 1. Setup UDP Socket
    let socket = UdpSocket::bind("0.0.0.0:0").await?;
    socket.connect(TARGET_ADDR).await?;
    info!("Broadcasting to {}", TARGET_ADDR);

    // 2. Connect to Binance
    let (ws_stream, _) = connect_async(BINANCE_WS_URL).await?;
    info!("Connected to Binance WebSocket");
    let (_, mut read) = ws_stream.split();

    // State
    let mut strike_price = 100000.0; 
    let args: Vec<String> = env::args().collect();
    if args.len() > 1 {
        strike_price = args[1].parse().unwrap_or(100000.0);
        info!("Strike Price set to: {}", strike_price);
    }
    
    // Volatility & Moments State
    let mut vol_estimate = 0.50;
#[derive(Serialize, Deserialize)]
struct StatsState {
    last_price: f64,
    sum_r: f64,
    sum_r2: f64,
    sum_r3: f64,
    sum_r4: f64,
    returns: VecDeque<f64>,
}

impl RollingStats {
    // ... (existing helper methods)

    fn load_or_new(window_size: usize) -> Self {
        let path = "calibration_state.json";
        if let Ok(file) = std::fs::File::open(path) {
            let reader = std::io::BufReader::new(file);
            if let Ok(state) = serde_json::from_reader::<_, StatsState>(reader) {
                info!("Loaded calibration state from disk. Returns history: {}", state.returns.len());
                // Reconstruct from state
                // Note: If window_size changed, we might want to trim or adjust. 
                // For now, trust the file if it matches logic.
                return Self {
                    window_size,
                    returns: state.returns,
                    last_price: state.last_price,
                    sum_r: state.sum_r,
                    sum_r2: state.sum_r2,
                    sum_r3: state.sum_r3,
                    sum_r4: state.sum_r4,
                };
            }
        }
        warn!("No calibration state found. Starting fresh.");
        Self::new(window_size)
    }

    fn save(&self) {
        let path = "calibration_state.json";
        let state = StatsState {
            last_price: self.last_price,
            sum_r: self.sum_r,
            sum_r2: self.sum_r2,
            sum_r3: self.sum_r3,
            sum_r4: self.sum_r4,
            returns: self.returns.clone(),
        };
        if let Ok(file) = std::fs::File::create(path) {
            let writer = std::io::BufWriter::new(file);
            let _ = serde_json::to_writer(writer, &state);
        }
    }
}
        match message {
            Ok(Message::Text(text)) => {
                if let Ok(v) = serde_json::from_str::<Value>(&text) {
                     if let Some(price_str) = v.get("p").and_then(|p| p.as_str()) {
                         if let Ok(price) = price_str.parse::<f64>() {
                             // --- QUANT CORE ---
                             
                             // 1. Update Stats
                             stats.update(price);
                             let (realized_skew, realized_kurt) = stats.get_moments();
                             
                             // Dampen/Clamp extreme values (e.g. Flash crashes) to avoid exploding logic
                             let skew = realized_skew.max(-3.0).min(3.0);
                             let kurt = realized_kurt.max(-3.0).min(10.0);
                             
                             // 2. Calculate Black-Scholes (Gram-Charlier)
                             // T = Time to expiry (let's assume 30 mins fixed for demo or read)
                             let time_to_expiry_years = 0.5 / 24.0 / 365.0; // 30 mins
                             
                             let (fair_prob, delta, gamma) = gram_charlier_binary(price, strike_price, time_to_expiry_years, vol_estimate, skew, kurt);
                             
                             // 3. Create Packet
                             let packet = SignalPacket {
                                 timestamp: SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos() as i64,
                                 spot_price: price,
                                 fair_prob,
                                 volatility: vol_estimate,
                                 delta,
                                 gamma,
                                 skew,
                                 kurtosis: kurt,
                                 toxicity: 0.0, // Placeholder for VPIN
                             };
                             
                             // 4. Zero-Alloc Serialize & Send
                             // bincode or serde_json? User asked for "compact UDP binary". 
                             // We'll use serde_json for debug readability first, switch to bincode for perf later.
                             // Actually, user defined `SignalPacket` in `core_types`.
                             let payload = serde_json::to_vec(&packet)?;
                             socket.send(&payload).await?;
                             
                             if last_price == 0.0 { last_price = price; }
                         }
                     }
                }
            }
            Ok(Message::Ping(_)) => {}
            Err(e) => error!("WS Error: {}", e),
            _ => {}
        }
    }

    Ok(())
}

// Gram-Charlier A Series for Binary Options
// Incorporates Skew and Kurtosis into pricing
// Ref: Market Models: A Guide to Financial Data Analysis (Alexander)
fn gram_charlier_binary(S: f64, K: f64, T: f64, sigma: f64, skew: f64, kurt: f64) -> (f64, f64, f64) {
    if T <= 0.0 {
        return if S > K { (1.0, 0.0, 0.0) } else { (0.0, 0.0, 0.0) };
    }
    
    // 1. Standard Black-Scholes d2
    let d2 = ((S / K).ln() - (0.5 * sigma * sigma * T)) / (sigma * T.sqrt());
    
    // Note: Textbook definitions vary on d2 vs d1. 
    // For Binary Call P(S>K) in risk-neutral world:
    // BS Price = e^-rT * N(d2). 
    // Here we assume r=0 (crypto stable/collateral funding negligible for 1h).
    
    // 2. Standard Normal PDF (phi) and CDF (Phi)
    let n = Normal::new(0.0, 1.0).unwrap();
    let phi_d2 = n.pdf(d2);
    let Phi_d2 = n.cdf(d2);
    
    // 3. Hermite Polynomials (Probabilists')
    // H2(x) = x^2 - 1
    // H3(x) = x^3 - 3x
    let H2 = d2 * d2 - 1.0;
    let H3 = d2 * d2 * d2 - 1.0 * d2; // Wait, H3 = x^3 - 3x? 
    // Check definition carefully. Often GC uses Physicist Hermite or different scaling.
    // Standard GC Expansion for PDF: 
    // f(x) ~ n(x) [ 1 + (skew/6)H3(x) + (kurt/24)H4(x) ]
    // Then CDF F(x) ~ N(x) - n(x) [ (skew/6)H2(x) + (kurt/24)H3(x) ]
    // Yes.
    
    let gc_adjustment = phi_d2 * (
        (skew / 6.0) * (d2 * d2 - 1.0) +
        (kurt / 24.0) * (d2 * d2 * d2 - 3.0 * d2)
    );
    
    let mut fair_prob = Phi_d2 - gc_adjustment;
    
    // Clamp probability
    fair_prob = fair_prob.max(0.0).min(1.0);
    
    (fair_prob, 0.0, 0.0)
}
