
# Rust HFT System (Phase 9)

**Status: Scaffolding**

This is the Rust-based High-Frequency Trading system implementing a Tokyo-London Arb Topology.

## Architecture

*   **Price Engine (`price_engine`)**:
    *   **Role**: The "Brain" (Tokyo).
    *   **Inputs**: Stream `btcusdt@aggTrade` from Binance (Tokyo Servers).
    *   **Logic**: Real-Time Volatility Tracking -> Black-Scholes Pricing.
    *   **Output**: UDP Packets (`SignalPacket`) to Execution Engine.
    *   **Why**: By processing volatility and pricing at the source (Binance), we save ~200ms vs doing it in London.

*   **Execution Engine (`execution_engine`)**:
    *   **Role**: The "Hand" (London).
    *   **Inputs**: UDP Stream from Price Engine.
    *   **Logic**: Inventory Skew (Avellaneda-Stoikov) + Quote Generation.
    *   **Output**: Limit Orders to Polymarket CLOB (London Servers).
    *   **Safety**: 
        *   **Dead Man's Switch**: Auto-cancel if UDP stream silent > 500ms.
        *   **Strict Maker**: Enforces `postOnly=true`.

## Prerequisites

*   Rust Stable Toolchain (`cargo`)
*   OpenSSL development headers (usually `pkg-config`, `libssl-dev` or `openssl` via brew).

## How to Run

1.  **Start Execution Engine (London Node):**
    ```bash
    cd execution_engine
    RUST_LOG=info cargo run
    ```

2.  **Start Price Engine (Tokyo Node):**
    ```bash
    cd price_engine
    RUST_LOG=info cargo run -- [StrikePrice]
    # Example: cargo run -- 105000
    ```

## Notes

*   **API Keys**: Currently using placeholders. Set `POLY_API_KEY`, `POLY_API_SECRET`, `POLY_API_PASSPHRASE` env vars.
*   **Networking**: Defaults to `127.0.0.1`. In production, set `TARGET_ADDR` to London IP.
