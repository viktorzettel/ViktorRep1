
import unittest
from unittest.mock import MagicMock
from orders import create_quote_orders
from py_clob_client.clob_types import PostOrdersArgs, OrderType

class TestPostOnly(unittest.TestCase):
    def test_post_only_default(self):
        client = MagicMock()
        client.create_order.return_value = MagicMock(order=MagicMock(maker="0x123"))
        
        # Call with default post_only (True)
        orders = create_quote_orders(client, "123", 0.5, 0.5, 10.0)
        
        self.assertEqual(len(orders), 2)
        # Check Bid
        self.assertTrue(orders[0].postOnly)
        self.assertEqual(orders[0].orderType, OrderType.GTC)
        # Check Ask
        self.assertTrue(orders[1].postOnly)
        self.assertEqual(orders[1].orderType, OrderType.GTC)
        
    def test_post_only_false(self):
        client = MagicMock()
        client.create_order.return_value = MagicMock(order=MagicMock(maker="0x123"))
        
        # Call with post_only=False
        orders = create_quote_orders(client, "123", 0.5, 0.5, 10.0, post_only=False)
        
        self.assertEqual(len(orders), 2)
        # Check Bid
        self.assertFalse(orders[0].postOnly)
        # Check Ask
        self.assertFalse(orders[1].postOnly)

if __name__ == '__main__':
    unittest.main()
