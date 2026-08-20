#!/usr/bin/env python3
"""
Simple test script to test LM Studio fix without external dependencies
"""

import sys
import os
import json

# Add the current directory to path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(current_dir, "core"))

# Import the router directly
try:
    from router import main as router_main
    
    def test_lmstudio_fix():
        """Test LM Studio fix through the router directly"""
        
        # Create payload that mimics what OpenWebUI would send
        payload = {
            "input": "fix lmstudio",
            "user_id": "test-user",
            "timestamp": "2025-10-07T19:20:00Z"
        }
        
        # Call the router main function
        try:
            result = router_main(payload)
            print("ROUTER RESULT:")
            print("=" * 50)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            print("=" * 50)
            
            # Check if there's an error
            if result.get("status") == "error":
                print(f"\n❌ ERROR DETECTED: {result.get('message', 'Unknown error')}")
                if "structured_data" in result:
                    print("Error details:", result["structured_data"])
            else:
                print("✅ SUCCESS: LM Studio fix completed")
                
            return result
        except Exception as e:
            print(f"ERROR in router: {e}")
            import traceback
            traceback.print_exc()
            return None

    if __name__ == "__main__":
        test_lmstudio_fix()
        
except ImportError as e:
    print(f"Cannot import router: {e}")
    print("Make sure you're running this from the AI Stack project root")