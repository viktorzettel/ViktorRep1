"""
Client wrapper for Polymarket CLOB API.

Provides a convenient wrapper around ClobClient with optimized initialization
that prefers saved L2 credentials to minimize startup latency.
"""

import logging
from typing import Optional

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

from config import settings


logger = logging.getLogger(__name__)


class PolymarketClient:
    """
    Wrapper around ClobClient with optimized credential handling.
    
    Prefers saved L2 credentials from environment variables to avoid
    the network round-trip of derive_api_key() on each startup.
    
    Usage:
        client = PolymarketClient()
        clob = client.get_client()
        # Use clob for trading operations
    """
    
    def __init__(self) -> None:
        """Initialize the Polymarket client with credentials from config."""
        self._client = ClobClient(
            host=settings.poly_host,
            key=settings.poly_private_key,
            chain_id=settings.poly_chain_id,
            funder=settings.poly_proxy_address,  # Support for Proxy Wallets
            signature_type=2 if settings.poly_proxy_address else None,  # 2 = Proxy Signature
        )
        self._creds: Optional[ApiCreds] = None
        
        # Auto-derive and set credentials
        self._initialize_credentials()
    
    def _initialize_credentials(self) -> None:
        """Initialize API credentials and set them on the client."""
        creds = self.derive_keys()
        self._client.set_api_creds(creds)
        self._creds = creds
        logger.info("Polymarket client initialized successfully")
    
    def derive_keys(self) -> ApiCreds:
        """
        Derive or load API credentials.
        
        Fast path: Uses pre-saved L2 credentials from environment if available.
        This avoids the network round-trip of create_or_derive_api_creds().
        
        Slow path: Falls back to deriving credentials from the private key
        if saved credentials are not available.
        
        Returns:
            ApiCreds: The API credentials for L2 authentication.
        """
        if settings.has_saved_credentials():
            logger.info("Using saved L2 credentials (fast startup)")
            creds = ApiCreds(
                api_key=settings.poly_api_key,
                api_secret=settings.poly_api_secret,
                api_passphrase=settings.poly_api_passphrase,
            )
            self._print_credentials(creds, derived=False)
            return creds
        
        logger.info("Deriving L2 credentials from private key (network call)...")
        creds = self._client.create_or_derive_api_creds()
        
        if creds is None:
            raise RuntimeError("Failed to derive API credentials from private key")
        
        self._print_credentials(creds, derived=True)
        return creds
    
    def _print_credentials(self, creds: ApiCreds, derived: bool) -> None:
        """
        Print credentials for user verification and saving.
        
        Args:
            creds: The API credentials to print.
            derived: Whether credentials were derived (True) or loaded (False).
        """
        source = "Derived" if derived else "Loaded"
        print(f"\n{'='*50}")
        print(f"{source} L2 API Credentials:")
        print(f"{'='*50}")
        print(f"POLY_API_KEY={creds.api_key}")
        print(f"POLY_API_SECRET={creds.api_secret}")
        print(f"POLY_API_PASSPHRASE={creds.api_passphrase}")
        print(f"{'='*50}")
        
        if derived:
            print("\nTIP: Save these credentials to your .env file for faster startup!")
        print()
    
    def get_client(self) -> ClobClient:
        """
        Get the initialized ClobClient instance.
        
        Returns:
            ClobClient: The authenticated client ready for trading operations.
        """
        return self._client
    
    def get_credentials(self) -> Optional[ApiCreds]:
        """
        Get the current API credentials.
        
        Returns:
            ApiCreds: The current credentials, or None if not initialized.
        """
        return self._creds
    
    @property
    def address(self) -> Optional[str]:
        """Get the wallet address associated with this client."""
        return self._client.get_address()

    def get_position(self, token_id: str) -> float:
        """
        Get current position for a specific token using the CLOB balance API.
        
        Args:
            token_id: Token ID to check.
            
        Returns:
            Current position size (float). Returns 0.0 if no position found.
        """
        if not token_id or token_id == "UNKNOWN":
            return 0.0
            
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            
            sig_type = 2 if settings.poly_proxy_address else 0
            
            data = self._client.get_balance_allowance(
                params=BalanceAllowanceParams(
                    asset_type=AssetType.CONDITIONAL,
                    token_id=token_id,
                    signature_type=sig_type
                )
            )
            
            if data:
                # Positions are strings in 6 decimals or raw integers depending on token type
                # For Conditional tokens, size is usually 6 decimals (same as USDC)
                raw_bal = data.get('balance', '0')
                return float(raw_bal) / 1_000_000
            
            return 0.0
            
        except Exception as e:
            logger.error(f"Error fetching position for {token_id[:12]}: {e}")
            return 0.0
