"""
Configuration module for Polymarket Market Maker.

Loads environment variables from .env file using pydantic-settings.
"""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    
    Required:
        - POLY_PRIVATE_KEY: Wallet private key for signing orders
    
    Optional (for faster startup):
        - POLY_API_KEY: Pre-saved API key
        - POLY_API_SECRET: Pre-saved API secret
        - POLY_API_PASSPHRASE: Pre-saved API passphrase
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # Required credentials
    poly_private_key: str = Field(
        ...,
        description="Wallet private key for signing orders",
    )
    
    # Optional L2 credentials for fast startup
    poly_api_key: Optional[str] = Field(
        default=None,
        description="Pre-saved API key (optional, speeds up startup)",
    )
    poly_api_secret: Optional[str] = Field(
        default=None,
        description="Pre-saved API secret (optional, speeds up startup)",
    )
    poly_api_passphrase: Optional[str] = Field(
        default=None,
        description="Pre-saved API passphrase (optional, speeds up startup)",
    )
    poly_proxy_address: Optional[str] = Field(None, alias="POLY_PROXY_ADDRESS")
    
    # Fixed configuration
    poly_host: str = Field(
        default="https://clob.polymarket.com",
        description="Polymarket CLOB API endpoint",
    )
    poly_chain_id: int = Field(
        default=137,
        description="Polygon mainnet chain ID",
    )
    
    def has_saved_credentials(self) -> bool:
        """Check if all L2 credentials are present for fast startup."""
        return all([
            self.poly_api_key,
            self.poly_api_secret,
            self.poly_api_passphrase,
        ])


# Singleton settings instance
settings = Settings()
