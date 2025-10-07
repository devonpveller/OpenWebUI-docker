#!/usr/bin/env python3
"""
Test script to simulate OpenWebUI calling the unified pipe function
"""

import sys
import os

# Add the scripts directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
ai_pipes_dir = os.path.join(current_dir, "scripts", "ai_pipes")
sys.path.append(ai_pipes_dir)

# Import the pipe function
from unified_openwebui_pipe import Pipe

def test_lmstudio_fix():
    """Test LM Studio fix through the pipe function"""
    
    # Create pipe instance
    pipe = Pipe()
    
    # Simulate OpenWebUI request body
    body = {
        "messages": [
            {
                "role": "user",
                "content": "fix lmstudio"
            }
        ],
        "timestamp": "2025-10-07T19:20:00Z"
    }
    
    # Simulate user object
    user = {
        "id": "test-user",
        "roles": ["user"]
    }
    
    # Call the pipe function
    try:
        result = pipe.pipe(body, user)
        print("PIPE FUNCTION RESULT:")
        print("=" * 50)
        print(result)
        print("=" * 50)
        return result
    except Exception as e:
        print(f"ERROR in pipe function: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    test_lmstudio_fix()