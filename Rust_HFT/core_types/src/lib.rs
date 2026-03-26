use serde::{Deserialize, Serialize};

/// Packet sent from PriceEngine (Tokyo) to ExecutionEngine (London).
/// Contains the Fair Price and Greeks derived from Binance Real-Time Data.
#[derive(Debug, Serialize, Deserialize, Clone, Copy)]
pub struct SignalPacket {
    /// Timestamp of the signal generation (Unix nanos)
    pub timestamp: i64,
    /// Fair Probability of "YES" (0.0 to 1.0) calculated via Black-Scholes
    pub fair_prob: f64,
    /// Underlying Spot Price (Binance)
    pub spot_price: f64,
    /// Implied/Realized Volatility used for pricing
    pub volatility: f64,
    /// Delta of the option (Sensitivity to price)
    pub delta: f64,
    /// Gamma of the option (Convexity)
    pub gamma: f64,
    /// Implied Skewness (3rd Moment)
    pub skew: f64,
    /// Implied Kurtosis (4th Moment)
    pub kurtosis: f64,
    /// VPIN or other toxic flow flag (1.0 = Toxic, 0.0 = Normal)
    pub toxicity: f64,
}

/// Simple Order Request internal structure
#[derive(Debug, Clone)]
pub struct OrderRequest {
    pub token_id: String,
    pub price: f64,
    pub size: f64,
    pub side: Side,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Side {
    Buy,
    Sell,
}
