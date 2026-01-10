import sys
import unittest
from unittest.mock import MagicMock, patch
import json
import os

# Mock modules before importing agent
sys.modules["streamlit"] = MagicMock()
sys.modules["dotenv"] = MagicMock()
sys.modules["langchain_openai"] = MagicMock()
sys.modules["langchain_core"] = MagicMock()
sys.modules["langchain_core.messages"] = MagicMock()
sys.modules["langgraph"] = MagicMock()
sys.modules["langgraph.graph"] = MagicMock()

# Configure streamlit mock secrets
st_mock = sys.modules["streamlit"]
st_mock.secrets.get.return_value = "fake_key"

# Append parent dir to path to import agent
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import get_restaurant_data, load_cache, save_cache, CACHE_FILE

class TestRestaurantAgent(unittest.TestCase):
    def setUp(self):
        # Clean up cache file before each test
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
            
    def tearDown(self):
        # Clean up cache file after each test
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)

    @patch("agent.requests.get")
    @patch("agent.SERP_API_KEY", "fake_key") # Mock the key so the function doesn't return early
    def test_caching_and_api_calls(self, mock_get):
        # Setup mock response for search
        mock_search_resp = {
            "local_results": [
                {"title": "Test Rest 1", "data_id": "id1", "rating": 4.5, "type": "Food"},
                {"title": "Test Rest 2", "data_id": "id2", "rating": 4.0, "type": "Food"}
            ]
        }
        
        # Setup mock response for reviews
        mock_review_resp = {"reviews": [{"snippet": "Good food"}]}
        
        # Configure side_effect to return different responses based on params
        def side_effect(url, params):
            if params["engine"] == "google_maps":
                return MagicMock(json=lambda: mock_search_resp)
            elif params["engine"] == "google_maps_reviews":
                return MagicMock(json=lambda: mock_review_resp)
            return MagicMock(json=lambda: {})
            
        mock_get.side_effect = side_effect

        # 1. Run first time - should call API (1 search + 2 restaurants * 3 keywords = 7 calls)
        # However, our code loops through keywords. 
        # get_restaurant_data logic: 
        #   search -> get places
        #   for p in places: 
        #     check cache
        #     if not cached: 
        #       for kw in ["送", "五星", "評論"]: request...
        #       save_cache
        
        results = get_restaurant_data("Taipei")
        self.assertEqual(len(results), 2)
        self.assertTrue(os.path.exists(CACHE_FILE))
        
        # Verify cache content
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        self.assertIn("id1", cache)
        self.assertIn("id2", cache)
        
        call_count_first_run = mock_get.call_count
        
        # 2. Run second time - should hit cache for id1 and id2
        # It will still call the initial search (1 call) but then skip the review calls
        results_2 = get_restaurant_data("Taipei")
        self.assertEqual(len(results_2), 2)
        
        call_count_second_run = mock_get.call_count
        
        # Should only have added 1 more call (the initial search)
        self.assertEqual(call_count_second_run - call_count_first_run, 1)

    @patch("agent.requests.get")
    @patch("agent.SERP_API_KEY", "fake_key")
    def test_rating_filter(self, mock_get):
        mock_search_resp = {
            "local_results": [
                {"title": "High Rating", "data_id": "id1", "rating": 4.8, "type": "Food"},
                {"title": "Low Rating", "data_id": "id2", "rating": 3.5, "type": "Food"}
            ]
        }
        
        # Mock review response - doesn't matter much for this test but needed to avoid crash
        mock_review_resp = {"reviews": []}

        def side_effect(url, params):
            if params["engine"] == "google_maps":
                return MagicMock(json=lambda: mock_search_resp)
            return MagicMock(json=lambda: mock_review_resp)
            
        mock_get.side_effect = side_effect

        # Filter > 4.0
        results = get_restaurant_data("Taipei", min_rating=4.0)
        
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "High Rating")

if __name__ == "__main__":
    unittest.main()
