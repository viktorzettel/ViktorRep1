#!/bin/bash

# Run Rust HFT System (Tokyo + London Nodes)
# Usage: ./run.sh [StrikePrice]

STRIKE=${1:-100000.0}

echo "BUILDING RUST HFT SYSTEM..."
cargo build --release

echo "STARTING LONDON NODE (EXECUTION ENGINE)..."
# Start in background
RUST_LOG=info ./target/release/execution_engine &
EXEC_PID=$!

echo "WAITING FOR LONDON NODE TO INITIALIZE..."
sleep 2

echo "STARTING TOKYO NODE (PRICE ENGINE)..."
# Start in background
RUST_LOG=info ./target/release/price_engine $STRIKE &
PRICE_PID=$!

echo "SYSTEM RUNNING. PRESS CTRL+C TO STOP."

# Cleanup on exit
trap "kill $EXEC_PID $PRICE_PID; exit" SIGINT SIGTERM

wait
