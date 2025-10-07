#!/usr/bin/env python3
"""
Test script to simulate OpenWebUI pipe function call for LM Studio fix
"""
import sys
import os

# Add the path to the router
sys.path.append(os.path.join(os.path.dirname(__file__), 'core'))

from router import main as router_main

def test_lmstudio_fix():
    """Test the LM Studio fix through the router like OpenWebUI pipe would"""
    
    # Simulate the payload that OpenWebUI pipe function would create
    payload = {
        "input": "fix lmstudio",
        "user_id": "test_user",
        "timestamp": "2025-10-07T12:00:00Z",
        "messages": [{"role": "user", "content": "fix lmstudio"}]
    }
    
    print("Testing LM Studio fix through router...")
    print(f"Payload: {payload}")
    print("\nRouter result:")
    
    try:
        result = router_main(payload)
        
        print(f"Status: {result.get('status')}")
        print(f"Module: {result.get('module_id')}")
        print(f"Service: {result.get('service')}")
        
        if 'structured_data' in result:
            action = result['structured_data'].get('action')
            print(f"Action executed: {action}")
        
        # Show first few lines of content
        content = result.get('content', '')
        content_lines = content.split('\n')[:5]
        print(f"Content preview: {content_lines}")
        
        return result
        
    except Exception as e:
        print(f"Error: {e}")
        return None

if __name__ == "__main__":
    test_lmstudio_fix()