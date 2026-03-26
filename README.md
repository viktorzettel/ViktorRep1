# ViktorRep1

A monorepo containing various AI, algorithmic trading, and web development projects.

## Core Projects

### 📈 Market Making & Algo Trading (`/MM`)
A comprehensive suite of algorithmic trading bots, quantitative analysis tools, and decision engines, primarily focused on crypto and prediction markets.
- **Kou Jump-Diffusion Bots**: Advanced options pricing models replacing Black-Scholes to better account for market jumps and fat tails (`kou_decision_bot.py`, `kou_dashboard.py`).
- **Volatility & Regime Trackers**: Real-time tracking of market volatility for Solana and XRP (`asset_vol_report.py`).
- **Arbitrage Loggers & Snipers**: Tools for identifying and executing arbitrage opportunities across markets (`arb_logger.py`, `momentum_sniper.py`).

### 💼 PortfolioManager
A modern web application for advanced portfolio optimization.
- Goes beyond traditional Mean-Variance Optimization using modern methods like Hierarchical Risk Parity (HRP) and Nested Clustered Optimization (NCO).
- [Live Demo](https://portfoliolens.netlify.app)

### 🪐 Interactive Web Visualizations
- **Real_Scale_Solar_System**: A visually stunning, accurately scaled 3D visualization of our solar system using React and Three.js. [Live Demo](https://realscalesolarsystem.netlify.app)
- **Jupiter / Saturn Projects**: Experimental 3D planetary renderings (`jupiterproject/`, `Saturn-descent/`). [Jupiter Live](https://jupiterproject.netlify.app)

### 🤖 AI Utilities & Agents
- **AIfluencer**: A Next.js application representing an AI influencer framework.
- **Bitcoin Sentiment Tracker**: A full-stack demo app (`bitcoin-sentiment-tracker-demo/`) for analyzing market sentiment.

### 🦀 Rust HFT (`/Rust_HFT`)
A high-frequency trading engine prototype built in Rust for maximum execution speed and minimal latency.

---
*Maintained by Viktor Zettel.*
